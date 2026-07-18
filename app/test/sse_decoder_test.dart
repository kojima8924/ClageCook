import 'dart:async';
import 'dart:convert';

import 'package:clage_cook/services/api_client.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  test('SSEをバイト分割・CRLF・複数data行越しで解析する', () async {
    const source =
        ': keepalive\r\n'
        'id: 7\r\n'
        'event: answer\r\n'
        'data: {"source":"claude",\r\n'
        'data: "ok":true,"text":"こんにちは"}\r\n'
        '\r\n';
    final bytes = utf8.encode(source);
    final chunks = <List<int>>[];
    for (var index = 0; index < bytes.length;) {
      final end = (index + 3).clamp(0, bytes.length);
      chunks.add(bytes.sublist(index, end));
      index = end;
    }

    final events = await SseDecoder.decode(
      Stream<List<int>>.fromIterable(chunks),
    ).toList();

    expect(events, hasLength(1));
    expect(events.single.event, 'answer');
    expect(events.single.id, '7');
    expect(events.single.data['source'], 'claude');
    expect(events.single.data['ok'], isTrue);
    expect(events.single.data['text'], 'こんにちは');
  });

  test('非JSONのdataも捨てずにrawで返す', () async {
    final events = await SseDecoder.decode(
      Stream.value(utf8.encode('event: notice\ndata: plain text\n\n')),
    ).toList();

    expect(events.single.event, 'notice');
    expect(events.single.data, {'raw': 'plain text'});
  });

  test('idは後続eventへ引き継ぎ、空idでリセットする', () async {
    final events = await SseDecoder.decode(
      Stream.value(
        utf8.encode(
          'id: 42\ndata: {"index":1}\n\n'
          'data: {"index":2}\n\n'
          'id:\ndata: {"index":3}\n\n'
          'data: {"index":4}\n\n',
        ),
      ),
    ).toList();

    expect(events.map((event) => event.id), ['42', '42', '', '']);
  });

  test('NULを含むidは無視し、空のevent名はmessageとして扱う', () async {
    final events = await SseDecoder.decode(
      Stream.value(
        utf8.encode(
          'id: safe\ndata: {"index":1}\n\n'
          'id: bad\u0000id\nevent:\ndata: {"index":2}\n\n',
        ),
      ),
    ).toList();

    expect(events.last.id, 'safe');
    expect(events.last.event, 'message');
  });
}
