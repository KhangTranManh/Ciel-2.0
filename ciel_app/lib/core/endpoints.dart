/// The one place that turns a user-entered server address into API URLs.
///
/// Accepts "host:port", "http(s)://host[:port][/prefix]" or a "ws(s)://.../ws" URL
/// and derives both the REST base and the WebSocket URL, so a reverse proxy that
/// serves Ciel under a path prefix works too.
class CielEndpoints {
  const CielEndpoints._(this.httpBase, this.webSocket);

  final Uri httpBase;
  final Uri webSocket;

  bool get isSecure => httpBase.scheme == 'https';

  static CielEndpoints? parse(String input) {
    var text = input.trim();
    if (text.isEmpty) return null;
    if (!text.contains('://')) text = 'http://$text';
    final uri = Uri.tryParse(text);
    if (uri == null || uri.host.isEmpty) return null;
    if (!const {'http', 'https', 'ws', 'wss'}.contains(uri.scheme)) return null;

    final secure = uri.scheme == 'https' || uri.scheme == 'wss';
    var prefix = uri.path;
    if (prefix.endsWith('/')) prefix = prefix.substring(0, prefix.length - 1);
    if (prefix.endsWith('/ws')) prefix = prefix.substring(0, prefix.length - 3);

    final port = uri.hasPort ? uri.port : null;
    final http = Uri(scheme: secure ? 'https' : 'http', host: uri.host, port: port, path: prefix);
    final ws = Uri(scheme: secure ? 'wss' : 'ws', host: uri.host, port: port, path: '$prefix/ws');
    return CielEndpoints._(http, ws);
  }

  Uri route(String path) => httpBase.replace(path: '${httpBase.path}$path');

  /// Browsers cannot set headers on a WebSocket, so the token travels as ?token=.
  Uri socketUri(String token) =>
      token.isEmpty ? webSocket : webSocket.replace(queryParameters: {'token': token});

  /// Human-readable address for the settings screen and banners.
  String get display => httpBase.toString();
}
