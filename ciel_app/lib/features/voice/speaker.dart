import 'dart:async';
import 'dart:typed_data';

import 'package:just_audio/just_audio.dart';

/// Plays audio produced by the backend's POST /tts, so speech formatting stays
/// server-side and sounds the same as the CLI.
abstract class Speaker {
  Stream<bool> get speaking;
  Future<void> play(Uint8List mp3);
  Future<void> stop();
}

class JustAudioSpeaker implements Speaker {
  final AudioPlayer _player = AudioPlayer();

  @override
  Stream<bool> get speaking => _player.playerStateStream
      .map((s) => s.playing && s.processingState != ProcessingState.completed)
      .distinct();

  @override
  Future<void> play(Uint8List mp3) async {
    await _player.stop();
    await _player.setAudioSource(_BytesSource(mp3));
    unawaited(_player.play());
  }

  @override
  Future<void> stop() => _player.stop();
}

class _BytesSource extends StreamAudioSource {
  _BytesSource(this._bytes);
  final Uint8List _bytes;

  @override
  Future<StreamAudioResponse> request([int? start, int? end]) async {
    start ??= 0;
    end ??= _bytes.length;
    return StreamAudioResponse(
      sourceLength: _bytes.length,
      contentLength: end - start,
      offset: start,
      stream: Stream.value(_bytes.sublist(start, end)),
      contentType: 'audio/mpeg',
    );
  }
}
