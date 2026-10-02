import 'package:flutter/material.dart';
import 'package:speech_to_text/speech_to_text.dart';

/// One-shot voice capture: tap, speak, and the final transcript is sent through
/// the same path as typed text.
class VoiceInputButton extends StatefulWidget {
  const VoiceInputButton({super.key, required this.onResult, required this.localeId, this.enabled = true});

  final ValueChanged<String> onResult;
  final String localeId;
  final bool enabled;

  @override
  State<VoiceInputButton> createState() => _VoiceInputButtonState();
}

class _VoiceInputButtonState extends State<VoiceInputButton> {
  final SpeechToText _stt = SpeechToText();
  bool _ready = false;
  bool _listening = false;
  bool _initTried = false;

  Future<bool> _ensureReady() async {
    if (_ready || _initTried) return _ready;
    _initTried = true;
    try {
      _ready = await _stt.initialize(
        onStatus: (s) {
          if (!mounted) return;
          if (s == SpeechToText.doneStatus || s == SpeechToText.notListeningStatus) {
            setState(() => _listening = false);
          }
        },
        onError: (_) {
          if (mounted) setState(() => _listening = false);
        },
      );
    } catch (_) {
      _ready = false;
    }
    if (!_ready && mounted) {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('Voice input is not available on this device.')),
      );
    }
    return _ready;
  }

  Future<void> _toggle() async {
    if (_listening) {
      await _stt.stop();
      setState(() => _listening = false);
      return;
    }
    if (!await _ensureReady()) return;
    setState(() => _listening = true);
    await _stt.listen(
      listenOptions: SpeechListenOptions(localeId: widget.localeId, partialResults: false, cancelOnError: true),
      onResult: (result) {
        if (!result.finalResult) return;
        final words = result.recognizedWords.trim();
        if (words.isNotEmpty) widget.onResult(words);
      },
    );
  }

  @override
  void dispose() {
    _stt.cancel();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return IconButton(
      tooltip: _listening ? 'Stop listening' : 'Speak a command',
      onPressed: widget.enabled ? _toggle : null,
      icon: Icon(_listening ? Icons.mic : Icons.mic_none),
      color: _listening ? Theme.of(context).colorScheme.secondary : null,
    );
  }
}
