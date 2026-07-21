import 'dart:convert';

import 'package:clage_cook/services/local_conversation_store.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  group('SharedPreferencesLocalConversationRepository', () {
    test('raw JSONをimmutable recordとmanifest commitで保存・復元する', () async {
      final values = MemoryLocalConversationValueStore();
      var now = DateTime.utc(2026, 7, 19, 1, 2, 3, 456);
      final repository = SharedPreferencesLocalConversationRepository(
        namespace: LocalConversationNamespace.directByok,
        valueStore: values,
        clock: () => now,
        idFactory: () => 'conversation-a',
      );

      final created = await repository.create(firstMessage: '  最初の   質問  ');
      expect(created.id, 'conversation-a');
      expect(created.storageRevision, 1);
      expect(created.value['title'], '最初の 質問');
      expect(created.value['created_at'], '2026-07-19T01:02:03.456Z');

      created.value['title'] = '呼び出し側だけの変更';
      expect((await repository.read(created.id))!.value['title'], '最初の 質問');

      now = DateTime.utc(2026, 7, 19, 1, 3);
      final current = (await repository.read(created.id))!;
      final saved = await repository.save(<String, dynamic>{
        ...current.value,
        'turns': <dynamic>[
          <String, dynamic>{
            'request_id': 'request-a',
            'message': '海について',
            'answers': <String, dynamic>{},
            'synthesis': <String, dynamic>{'text': '海のまとめです。'},
          },
        ],
      }, expectedStorageRevision: current.storageRevision);

      expect(saved.storageRevision, 2);
      expect(saved.value['created_at'], '2026-07-19T01:02:03.456Z');
      expect(saved.value['updated_at'], '2026-07-19T01:03:00.000Z');
      final summaries = await repository.list();
      expect(summaries, hasLength(1));
      expect(summaries.single.turnCount, 1);
      expect(summaries.single.preview, '海のまとめです。');

      final manifest = values.values.entries.singleWhere(
        (entry) => entry.key.endsWith('.manifest'),
      );
      final manifestJson = jsonDecode(manifest.value) as Map<String, dynamic>;
      expect(manifestJson['manifest_revision'], 2);
      expect(manifestJson['entries'], <String, dynamic>{'conversation-a': 2});
      expect(
        values.values.keys.where((key) => key.contains('.record.')),
        hasLength(1),
      );
    });

    test('manifest write失敗では公開前recordを捨てて旧revisionを保つ', () async {
      final values = _FailingValueStore();
      final repository = SharedPreferencesLocalConversationRepository(
        namespace: LocalConversationNamespace.directByok,
        valueStore: values,
        idFactory: () => 'conversation-a',
      );
      final created = await repository.create(firstMessage: 'old');
      values.failNextManifestWrite = true;

      await expectLater(
        repository.save(<String, dynamic>{
          ...created.value,
          'title': 'new',
        }, expectedStorageRevision: created.storageRevision),
        throwsA(isA<StateError>()),
      );

      final loaded = await repository.read(created.id);
      expect(loaded!.storageRevision, 1);
      expect(loaded.value['title'], 'old');
      expect(
        values.values.keys.where((key) => key.contains('.record.')),
        hasLength(1),
      );
    });

    test('record write失敗ではmanifestを変更しない', () async {
      final values = _FailingValueStore();
      final repository = SharedPreferencesLocalConversationRepository(
        namespace: LocalConversationNamespace.directByok,
        valueStore: values,
        idFactory: () => 'conversation-a',
      );
      final created = await repository.create(firstMessage: 'old');
      final manifestBefore = values.values.entries
          .singleWhere((entry) => entry.key.endsWith('.manifest'))
          .value;
      values.failNextRecordWrite = true;

      await expectLater(
        repository.rename(
          created.id,
          'new',
          expectedStorageRevision: created.storageRevision,
        ),
        throwsA(isA<StateError>()),
      );

      expect(
        values.values.entries
            .singleWhere((entry) => entry.key.endsWith('.manifest'))
            .value,
        manifestBefore,
      );
      expect((await repository.read(created.id))!.value['title'], 'old');
    });

    test('direct BYOKとreference server、server同士の履歴を混在させない', () async {
      final values = MemoryLocalConversationValueStore();
      final direct = SharedPreferencesLocalConversationRepository(
        namespace: LocalConversationNamespace.directByok,
        valueStore: values,
      );
      final referenceA = SharedPreferencesLocalConversationRepository(
        namespace: LocalConversationNamespace.referenceServer(
          'https://Example.test/clage/',
        ),
        valueStore: values,
      );
      final referenceB = SharedPreferencesLocalConversationRepository(
        namespace: LocalConversationNamespace.referenceServer(
          'https://other.example.test',
        ),
        valueStore: values,
      );

      await direct.create(firstMessage: 'direct', conversationId: 'same-id');
      await referenceA.create(
        firstMessage: 'reference-a',
        conversationId: 'same-id',
      );
      await referenceB.create(
        firstMessage: 'reference-b',
        conversationId: 'same-id',
      );

      expect((await direct.read('same-id'))!.value['title'], 'direct');
      expect((await referenceA.read('same-id'))!.value['title'], 'reference-a');
      expect((await referenceB.read('same-id'))!.value['title'], 'reference-b');
      expect(
        values.values.keys.where((key) => key.endsWith('.manifest')),
        hasLength(3),
      );
      expect(
        LocalConversationNamespace.referenceServer(
          'https://Example.test/clage/',
        ),
        LocalConversationNamespace.referenceServer(
          'https://example.test/clage',
        ),
      );
    });

    test('検索、rename、memory更新、JSON exportをrevision付きで行う', () async {
      final values = MemoryLocalConversationValueStore();
      var now = DateTime.utc(2026, 7, 19, 2);
      final repository = SharedPreferencesLocalConversationRepository(
        namespace: LocalConversationNamespace.directByok,
        valueStore: values,
        clock: () => now,
      );
      final created = await repository.create(
        firstMessage: '紅茶の相談',
        conversationId: 'tea',
      );
      final withTurn = await repository.save(<String, dynamic>{
        ...created.value,
        'turns': <dynamic>[
          <String, dynamic>{
            'request_id': 'turn-1',
            'message': '茶葉を蒸らす時間',
            'clean_message': '茶葉を蒸らす時間',
            'answers': <String, dynamic>{
              'claude': <String, dynamic>{'text': '三分が目安です'},
            },
            'synthesis': <String, dynamic>{'text': '湯温にも注意します'},
          },
        ],
      }, expectedStorageRevision: created.storageRevision);
      now = DateTime.utc(2026, 7, 19, 2, 1);
      final memory = await repository.updateMemory(
        conversationId: created.id,
        expectedMemoryRevision: 0,
        expectedStorageRevision: withTurn.storageRevision,
        text: 'アッサムを使う',
      );
      final renamed = await repository.rename(
        created.id,
        '朝の紅茶',
        expectedStorageRevision: memory.storageRevision,
      );

      expect(await repository.search('三分 湯温'), hasLength(1));
      expect(await repository.search('アッサム'), hasLength(1));
      expect(await repository.search('コーヒー'), isEmpty);
      expect(renamed.value['memory'], containsPair('revision', 1));
      expect(renamed.value['memory'], containsPair('text', 'アッサムを使う'));

      await expectLater(
        repository.rename(
          created.id,
          'stale',
          expectedStorageRevision: withTurn.storageRevision,
        ),
        throwsA(isA<LocalConversationConflict>()),
      );
      await expectLater(
        repository.updateMemory(
          conversationId: created.id,
          expectedMemoryRevision: 0,
          text: 'stale',
        ),
        throwsA(isA<LocalConversationConflict>()),
      );

      final exported = await repository.exportJson(created.id);
      expect(exported.endsWith('\n'), isTrue);
      final decoded = jsonDecode(exported) as Map<String, dynamic>;
      expect(decoded['title'], '朝の紅茶');
      expect(decoded, isNot(contains('storage_revision')));
      expect(decoded, isNot(contains('namespace')));
    });

    test('forkは対象turn直前だけを複製し親を変更しない', () async {
      final values = MemoryLocalConversationValueStore();
      final repository = SharedPreferencesLocalConversationRepository(
        namespace: LocalConversationNamespace.directByok,
        valueStore: values,
      );
      final parent = await repository.create(
        firstMessage: '親会話',
        conversationId: 'parent',
      );
      final saved = await repository.save(<String, dynamic>{
        ...parent.value,
        'memory': <String, dynamic>{
          'revision': 2,
          'text': '制約',
          'updated_at': '2026-07-19T00:00:00.000Z',
        },
        'turns': <dynamic>[
          <String, dynamic>{'request_id': 'turn-1', 'message': 'one'},
          <String, dynamic>{'request_id': 'turn-2', 'message': 'two'},
          <String, dynamic>{'request_id': 'turn-3', 'message': 'three'},
        ],
      }, expectedStorageRevision: parent.storageRevision);

      final branch = await repository.fork(
        conversationId: parent.id,
        beforeTurnIndex: 1,
        parentTurnRequestId: 'turn-2',
        branchConversationId: 'branch',
        expectedStorageRevision: saved.storageRevision,
      );

      expect(branch.value['turns'], hasLength(1));
      expect((branch.value['turns'] as List).single['request_id'], 'turn-1');
      expect(branch.value['memory'], containsPair('text', '制約'));
      expect(
        branch.value['branch'],
        containsPair('parent_conversation_id', 'parent'),
      );
      expect(
        branch.value['branch'],
        containsPair('parent_turn_request_id', 'turn-2'),
      );
      expect((await repository.read('parent'))!.value['turns'], hasLength(3));

      await expectLater(
        repository.fork(
          conversationId: parent.id,
          beforeTurnIndex: 1,
          parentTurnRequestId: 'wrong-turn',
        ),
        throwsStateError,
      );
    });

    test('deleteはmanifest commit後に会話record familyを除去する', () async {
      final values = MemoryLocalConversationValueStore();
      final repository = SharedPreferencesLocalConversationRepository(
        namespace: LocalConversationNamespace.directByok,
        valueStore: values,
      );
      final created = await repository.create(
        firstMessage: 'delete me',
        conversationId: 'delete-me',
      );
      await repository.delete(
        created.id,
        expectedStorageRevision: created.storageRevision,
      );

      expect(await repository.read(created.id), isNull);
      expect(await repository.list(), isEmpty);
      expect(
        values.values.keys.where((key) => key.contains('.record.')),
        isEmpty,
      );
      await expectLater(
        repository.exportJson(created.id),
        throwsA(isA<LocalConversationNotFound>()),
      );
    });

    test('manifestが参照するrecord欠損は空会話へ降格せずcorruptionにする', () async {
      final values = MemoryLocalConversationValueStore();
      final repository = SharedPreferencesLocalConversationRepository(
        namespace: LocalConversationNamespace.directByok,
        valueStore: values,
      );
      final created = await repository.create(conversationId: 'conversation-a');
      final recordKey = values.values.keys.singleWhere(
        (key) => key.contains('.record.'),
      );
      values.values.remove(recordKey);

      await expectLater(
        repository.read(created.id),
        throwsA(isA<LocalConversationCorruption>()),
      );
    });

    test('compactはmanifestから到達不能なcrash orphanだけを除去する', () async {
      final values = MemoryLocalConversationValueStore();
      final repository = SharedPreferencesLocalConversationRepository(
        namespace: LocalConversationNamespace.directByok,
        valueStore: values,
      );
      await repository.create(conversationId: 'conversation-a');
      final currentRecord = values.values.keys.singleWhere(
        (key) => key.contains('.record.'),
      );
      final orphan =
          '${currentRecord.substring(0, currentRecord.lastIndexOf('.') + 1)}999';
      values.values[orphan] = '{"orphan":true}';

      await repository.compact();

      expect(values.values, contains(currentRecord));
      expect(values.values, isNot(contains(orphan)));
    });

    test('同じnamespaceの複数repository instanceもmanifest更新を直列化する', () async {
      final values = MemoryLocalConversationValueStore();
      final first = SharedPreferencesLocalConversationRepository(
        namespace: LocalConversationNamespace.directByok,
        valueStore: values,
        idFactory: () => 'first',
      );
      final second = SharedPreferencesLocalConversationRepository(
        namespace: LocalConversationNamespace.directByok,
        valueStore: values,
        idFactory: () => 'second',
      );

      await Future.wait([
        first.create(firstMessage: 'one'),
        second.create(firstMessage: 'two'),
      ]);

      expect(
        (await first.list()).map((item) => item.id),
        containsAll(['first', 'second']),
      );
    });
  });
}

class _FailingValueStore extends MemoryLocalConversationValueStore {
  bool failNextManifestWrite = false;
  bool failNextRecordWrite = false;

  @override
  Future<void> write(String key, String value) async {
    if (key.endsWith('.manifest') && failNextManifestWrite) {
      failNextManifestWrite = false;
      throw StateError('simulated manifest failure');
    }
    if (key.contains('.record.') && failNextRecordWrite) {
      failNextRecordWrite = false;
      throw StateError('simulated record failure');
    }
    await super.write(key, value);
  }
}
