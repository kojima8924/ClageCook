import 'dart:async';
import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:http/http.dart' as http;

void main() => runApp(const ClageCookApp());

/// 会議に参加するAI(表示順)と見た目
const kBackends = ['claude', 'gemini', 'chatgpt', 'grok'];
const kLabels = {
  'claude': 'Claude',
  'gemini': 'Gemini',
  'chatgpt': 'ChatGPT',
  'grok': 'Grok',
};
const kColors = {
  'claude': Color(0xFFD97757),
  'gemini': Color(0xFF4285F4),
  'chatgpt': Color(0xFF10A37F),
  'grok': Color(0xFF8B95A5),
};

class ClageCookApp extends StatelessWidget {
  const ClageCookApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'Clage Cook',
      debugShowCheckedModeBanner: false,
      theme: ThemeData(
        colorScheme: ColorScheme.fromSeed(
          seedColor: const Color(0xFF3B6EA5),
          brightness: Brightness.dark,
        ),
        useMaterial3: true,
      ),
      home: const ChatScreen(),
    );
  }
}

/// 1つのAIの回答状態
class Answer {
  final String source;
  final bool loading;
  final bool ok;
  final String text;
  const Answer(this.source, {this.loading = true, this.ok = false, this.text = ''});
}

class ChatScreen extends StatefulWidget {
  const ChatScreen({super.key});
  @override
  State<ChatScreen> createState() => _ChatScreenState();
}

class _ChatScreenState extends State<ChatScreen> {
  // TODO: 設定画面でサーバURLを変更可能にする(モバイル実機は 10.0.2.2 やLAN IP)
  static const _baseUrl = 'http://127.0.0.1:8000';

  final _controller = TextEditingController();
  Map<String, Answer> _answers = {};
  String? _synthesis;
  bool _synthLoading = false;
  bool _sending = false;
  String _mode = '';
  String _error = '';

  Future<void> _send() async {
    final msg = _controller.text.trim();
    if (msg.isEmpty || _sending) return;
    setState(() {
      _sending = true;
      _error = '';
      _answers = {for (final b in kBackends) b: Answer(b)};
      _synthesis = null;
      _synthLoading = true;
    });
    try {
      final req = http.Request('POST', Uri.parse('$_baseUrl/api/chat'));
      req.headers['Content-Type'] = 'application/json';
      req.body = jsonEncode({'message': msg});
      final resp = await http.Client().send(req);
      var event = '';
      await for (final line in resp.stream
          .transform(utf8.decoder)
          .transform(const LineSplitter())) {
        if (line.startsWith('event:')) {
          event = line.substring(6).trim();
        } else if (line.startsWith('data:')) {
          _handle(event, jsonDecode(line.substring(5).trim()));
        }
      }
    } catch (e) {
      setState(() => _error = '接続に失敗しました: $e');
    } finally {
      setState(() {
        _sending = false;
        _synthLoading = false;
      });
    }
  }

  void _handle(String event, dynamic data) {
    setState(() {
      switch (event) {
        case 'meta':
          _mode = (data['mode'] ?? '') as String;
          break;
        case 'answer':
          final s = data['source'] as String;
          _answers[s] = Answer(
            s,
            loading: false,
            ok: data['ok'] == true,
            text: (data['text'] ?? data['error'] ?? '') as String,
          );
          break;
        case 'synthesis':
          _synthesis = data['ok'] == true
              ? (data['text'] as String)
              : (data['error'] ?? '統合に失敗しました') as String;
          _synthLoading = false;
          break;
      }
    });
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('Clage Cook'),
        actions: [
          if (_mode.isNotEmpty)
            Center(
              child: Padding(
                padding: const EdgeInsets.symmetric(horizontal: 16),
                child: Text('mode: $_mode',
                    style: Theme.of(context).textTheme.labelMedium),
              ),
            ),
        ],
      ),
      body: Column(
        children: [
          if (_error.isNotEmpty)
            Container(
              width: double.infinity,
              color: Theme.of(context).colorScheme.errorContainer,
              padding: const EdgeInsets.all(8),
              child: Text(_error),
            ),
          Expanded(
            child: ListView(
              padding: const EdgeInsets.all(12),
              children: [
                for (final b in kBackends)
                  _AnswerCard(answer: _answers[b] ?? Answer(b, loading: false)),
                if (_synthLoading || _synthesis != null)
                  _SynthCard(text: _synthesis, loading: _synthLoading),
                const SizedBox(height: 80),
              ],
            ),
          ),
          _inputBar(context),
        ],
      ),
    );
  }

  Widget _inputBar(BuildContext context) {
    return SafeArea(
      child: Padding(
        padding: const EdgeInsets.fromLTRB(8, 4, 8, 8),
        child: Row(
          children: [
            Expanded(
              child: TextField(
                controller: _controller,
                onSubmitted: (_) => _send(),
                decoration: const InputDecoration(
                  hintText: '質問を入力',
                  border: OutlineInputBorder(),
                  isDense: true,
                ),
              ),
            ),
            const SizedBox(width: 8),
            IconButton.filled(
              onPressed: _sending ? null : _send,
              icon: _sending
                  ? const SizedBox(
                      width: 18,
                      height: 18,
                      child: CircularProgressIndicator(strokeWidth: 2),
                    )
                  : const Icon(Icons.send),
            ),
          ],
        ),
      ),
    );
  }
}

class _AnswerCard extends StatelessWidget {
  final Answer answer;
  const _AnswerCard({required this.answer});

  @override
  Widget build(BuildContext context) {
    final color = kColors[answer.source] ?? Colors.grey;
    return Card(
      margin: const EdgeInsets.only(bottom: 8),
      child: Padding(
        padding: const EdgeInsets.all(12),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Container(width: 4, height: 16, color: color),
                const SizedBox(width: 8),
                Text(kLabels[answer.source] ?? answer.source,
                    style: TextStyle(color: color, fontWeight: FontWeight.bold)),
              ],
            ),
            const SizedBox(height: 6),
            if (answer.loading)
              const Text('回答待ち...', style: TextStyle(color: Colors.grey))
            else
              Text(answer.ok ? answer.text : '⚠ ${answer.text}'),
          ],
        ),
      ),
    );
  }
}

class _SynthCard extends StatelessWidget {
  final String? text;
  final bool loading;
  const _SynthCard({required this.text, required this.loading});

  @override
  Widget build(BuildContext context) {
    return Card(
      color: Theme.of(context).colorScheme.primaryContainer,
      margin: const EdgeInsets.only(bottom: 8),
      child: Padding(
        padding: const EdgeInsets.all(12),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const Text('統合回答', style: TextStyle(fontWeight: FontWeight.bold)),
            const SizedBox(height: 6),
            if (loading)
              const Text('統合中...', style: TextStyle(color: Colors.grey))
            else
              Text(text ?? ''),
          ],
        ),
      ),
    );
  }
}
