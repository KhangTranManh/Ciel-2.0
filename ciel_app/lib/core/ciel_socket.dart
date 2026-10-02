import 'dart:async';

import 'package:web_socket_channel/web_socket_channel.dart';

import 'protocol.dart';

enum ConnectionStatus { idle, connecting, open, closed, unauthorized }

/// The only thing that talks to the server's /ws endpoint. Screens never touch it
/// directly; they go through CielController, which keeps input/output modalities
/// (keyboard, voice, TTS) interchangeable.
abstract class CielTransport {
  Stream<ServerMessage> get messages;
  Stream<ConnectionStatus> get statusChanges;
  ConnectionStatus get status;

  void connect(Uri uri);
  void reconnectNow();

  /// False when the socket is not open — callers must not pretend it was sent.
  bool send(String payload);

  Future<void> disconnect();
}

typedef ChannelFactory = WebSocketChannel Function(Uri uri);

class CielSocket implements CielTransport {
  CielSocket({ChannelFactory? channelFactory, this.retryDelay = const Duration(seconds: 2)})
      : _factory = channelFactory ?? WebSocketChannel.connect;

  static const unauthorizedCloseCode = 4401;

  final ChannelFactory _factory;
  final Duration retryDelay;
  final _messages = StreamController<ServerMessage>.broadcast();
  final _statuses = StreamController<ConnectionStatus>.broadcast();

  ConnectionStatus _status = ConnectionStatus.idle;
  Uri? _uri;
  WebSocketChannel? _channel;
  StreamSubscription<dynamic>? _subscription;
  Timer? _retry;
  bool _wantConnected = false;
  bool _rejected = false;
  int _generation = 0;

  @override
  Stream<ServerMessage> get messages => _messages.stream;

  @override
  Stream<ConnectionStatus> get statusChanges => _statuses.stream;

  @override
  ConnectionStatus get status => _status;

  @override
  void connect(Uri uri) {
    _uri = uri;
    _wantConnected = true;
    _open();
  }

  @override
  void reconnectNow() {
    if (_uri == null || _status == ConnectionStatus.open || _status == ConnectionStatus.connecting) return;
    _wantConnected = true;
    _retry?.cancel();
    _retry = null;
    _open();
  }

  Future<void> _open() async {
    final uri = _uri;
    if (uri == null) return;
    final generation = ++_generation;
    _rejected = false;
    _setStatus(ConnectionStatus.connecting);

    final WebSocketChannel channel;
    try {
      channel = _factory(uri);
      await channel.ready;
    } catch (_) {
      if (generation == _generation) {
        _setStatus(ConnectionStatus.closed);
        _scheduleRetry();
      }
      return;
    }
    if (generation != _generation) {
      channel.sink.close();
      return;
    }

    _channel = channel;
    _setStatus(ConnectionStatus.open);
    _subscription = channel.stream.listen(
      (data) {
        final message = ServerMessage.parse('$data');
        if (message is ErrorMessage && message.isUnauthorized) _rejected = true;
        _messages.add(message);
      },
      onDone: () => _onClosed(generation, channel.closeCode),
      onError: (_) {},
      cancelOnError: false,
    );
  }

  void _onClosed(int generation, int? closeCode) {
    if (generation != _generation) return;
    _channel = null;
    _subscription = null;
    if (_rejected || closeCode == unauthorizedCloseCode) {
      _setStatus(ConnectionStatus.unauthorized);
      return;
    }
    _setStatus(ConnectionStatus.closed);
    _scheduleRetry();
  }

  void _scheduleRetry() {
    if (!_wantConnected || _retry != null) return;
    _retry = Timer(retryDelay, () {
      _retry = null;
      if (_wantConnected) _open();
    });
  }

  void _setStatus(ConnectionStatus value) {
    if (_status == value) return;
    _status = value;
    _statuses.add(value);
  }

  @override
  bool send(String payload) {
    final channel = _channel;
    if (_status != ConnectionStatus.open || channel == null) return false;
    channel.sink.add(payload);
    return true;
  }

  @override
  Future<void> disconnect() async {
    _wantConnected = false;
    _generation++;
    _retry?.cancel();
    _retry = null;
    await _subscription?.cancel();
    _subscription = null;
    await _channel?.sink.close();
    _channel = null;
    _setStatus(ConnectionStatus.idle);
  }

  Future<void> dispose() async {
    await disconnect();
    await _messages.close();
    await _statuses.close();
  }
}
