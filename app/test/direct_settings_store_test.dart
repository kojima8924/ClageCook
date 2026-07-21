import 'package:clage_cook/services/direct_settings_store.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  group('DirectSettings', () {
    test('Direct BYOKとAutoを安全な既定値にする', () {
      const settings = DirectSettings();

      expect(settings.executionMode, ExecutionMode.directByok);
      expect(settings.reasoningMode, ReasoningMode.auto);
      expect(settings.showTokenUsageLedger, isTrue);
      expect(settings.showLiveApiConfirmation, isTrue);
      expect(settings.synthesizerProvider, isNull);
      expect(settings.hasAnyKey, isFalse);
      expect(settings.hasAllKeys, isFalse);
      for (final provider in DirectProvider.values) {
        expect(settings.hasKey(provider), isFalse);
        expect(settings.status(provider), DirectApiKeyStatus.missing);
        expect(settings.modelOverrideFor(provider), isEmpty);
      }
    });

    test('copyWithは値の更新、キー消去、synthesizerのAuto復帰に対応する', () {
      const original = DirectSettings(
        executionMode: ExecutionMode.referenceServer,
        reasoningMode: ReasoningMode.high,
        claudeApiKey: 'claude-key',
        chatGptApiKey: 'chatgpt-key',
        geminiApiKey: 'gemini-key',
        grokApiKey: 'grok-key',
        synthesizerProvider: DirectProvider.claude,
      );

      final changed = original.copyWith(
        executionMode: ExecutionMode.directByok,
        showTokenUsageLedger: false,
        showLiveApiConfirmation: false,
        claudeApiKey: '',
        synthesizerProvider: null,
      );

      expect(changed.executionMode, ExecutionMode.directByok);
      expect(changed.reasoningMode, ReasoningMode.high);
      expect(changed.showTokenUsageLedger, isFalse);
      expect(changed.showLiveApiConfirmation, isFalse);
      expect(changed.hasKey(DirectProvider.claude), isFalse);
      expect(changed.hasKey(DirectProvider.chatgpt), isTrue);
      expect(changed.hasAllKeys, isFalse);
      expect(changed.synthesizerProvider, isNull);
      expect(changed.openAiApiKey, 'chatgpt-key');
      expect(changed.anthropicApiKey, isEmpty);
      expect(changed.xAiApiKey, 'grok-key');
    });
  });

  group('DirectSettingsStore', () {
    test('非秘密設定と4社APIキーを同じrevisionへ結び付けて復元する', () async {
      final publicStore = _MemoryValueStore();
      final secretStore = _MemoryValueStore();
      final repository = DirectSettingsStore(
        publicStore: publicStore,
        secretStore: secretStore,
        revisionFactory: () => 'revision-a',
      );

      await repository.save(
        const DirectSettings(
          executionMode: ExecutionMode.referenceServer,
          reasoningMode: ReasoningMode.high,
          showTokenUsageLedger: false,
          showLiveApiConfirmation: false,
          claudeApiKey: ' claude-secret ',
          chatGptApiKey: 'chatgpt-secret',
          geminiApiKey: 'gemini-secret',
          grokApiKey: 'grok-secret',
          claudeModelOverride: ' claude-model ',
          chatGptModelOverride: 'chatgpt-model',
          geminiModelOverride: 'gemini-model',
          grokModelOverride: 'grok-model',
          synthesizerProvider: DirectProvider.gemini,
        ),
      );

      final loaded = await repository.load();
      expect(loaded.executionMode, ExecutionMode.referenceServer);
      expect(loaded.reasoningMode, ReasoningMode.high);
      expect(loaded.showTokenUsageLedger, isFalse);
      expect(loaded.showLiveApiConfirmation, isFalse);
      expect(loaded.claudeApiKey, 'claude-secret');
      expect(loaded.chatGptApiKey, 'chatgpt-secret');
      expect(loaded.geminiApiKey, 'gemini-secret');
      expect(loaded.grokApiKey, 'grok-secret');
      expect(loaded.claudeModelOverride, 'claude-model');
      expect(loaded.chatGptModelOverride, 'chatgpt-model');
      expect(loaded.geminiModelOverride, 'gemini-model');
      expect(loaded.grokModelOverride, 'grok-model');
      expect(loaded.synthesizerProvider, DirectProvider.gemini);
      expect(loaded.hasAllKeys, isTrue);

      final publicRecord = publicStore.values.values.single;
      for (final secret in const [
        'claude-secret',
        'chatgpt-secret',
        'gemini-secret',
        'grok-secret',
      ]) {
        expect(publicRecord, isNot(contains(secret)));
      }
      expect(publicRecord, contains('claude-model'));
      expect(secretStore.values.values.single, contains('claude-secret'));
    });

    test('AUTO・LOW・MEDIUM・HIGHの既定effortを欠落なく往復保存する', () async {
      final publicStore = _MemoryValueStore();
      final secretStore = _MemoryValueStore();
      var revision = 0;
      final repository = DirectSettingsStore(
        publicStore: publicStore,
        secretStore: secretStore,
        revisionFactory: () => 'reasoning-${++revision}',
      );

      for (final mode in ReasoningMode.values) {
        await repository.save(DirectSettings(reasoningMode: mode));
        expect((await repository.load()).reasoningMode, mode);
      }
    });

    test('実API確認表示だけを更新して秘密recordとAPIキーを維持する', () async {
      final publicStore = _MemoryValueStore();
      final secretStore = _MemoryValueStore();
      final repository = DirectSettingsStore(
        publicStore: publicStore,
        secretStore: secretStore,
        revisionFactory: () => 'revision-a',
      );
      await repository.save(
        const DirectSettings(
          claudeApiKey: 'must-stay-secret',
          chatGptApiKey: 'another-secret',
        ),
      );
      final secretRecordBefore = secretStore.values.values.single;

      await repository.setShowLiveApiConfirmation(false);

      expect(secretStore.values.values.single, secretRecordBefore);
      final loaded = await repository.load();
      expect(loaded.showLiveApiConfirmation, isFalse);
      expect(loaded.claudeApiKey, 'must-stay-secret');
      expect(loaded.chatGptApiKey, 'another-secret');
    });

    test('秘密側write失敗では公開設定を変更せず平文へ降格しない', () async {
      final publicStore = _MemoryValueStore();
      final secretStore = _MemoryValueStore();
      final revisions = ['revision-a', 'revision-b'].iterator;
      final repository = DirectSettingsStore(
        publicStore: publicStore,
        secretStore: secretStore,
        revisionFactory: () {
          revisions.moveNext();
          return revisions.current;
        },
      );
      await repository.save(const DirectSettings(claudeApiKey: 'old-secret'));
      secretStore.failNextWrite = true;

      await expectLater(
        repository.save(
          const DirectSettings(
            executionMode: ExecutionMode.referenceServer,
            claudeApiKey: 'new-secret',
          ),
        ),
        throwsA(isA<StateError>()),
      );

      final loaded = await repository.load();
      expect(loaded.executionMode, ExecutionMode.directByok);
      expect(loaded.claudeApiKey, 'old-secret');
      expect(publicStore.values.values.single, isNot(contains('new-secret')));
    });

    test('公開側write失敗後はrevision不一致の新旧APIキーを使用しない', () async {
      final publicStore = _MemoryValueStore();
      final secretStore = _MemoryValueStore();
      final revisions = ['revision-a', 'revision-b'].iterator;
      final repository = DirectSettingsStore(
        publicStore: publicStore,
        secretStore: secretStore,
        revisionFactory: () {
          revisions.moveNext();
          return revisions.current;
        },
      );
      await repository.save(const DirectSettings(claudeApiKey: 'old-secret'));
      publicStore.failNextWrite = true;

      await expectLater(
        repository.save(
          const DirectSettings(
            executionMode: ExecutionMode.referenceServer,
            claudeApiKey: 'new-secret',
          ),
        ),
        throwsA(isA<StateError>()),
      );

      final loaded = await repository.load();
      expect(loaded.executionMode, ExecutionMode.directByok);
      expect(loaded.hasAnyKey, isFalse);
      expect(publicStore.values.values.single, isNot(contains('new-secret')));
    });

    test('secure storage read失敗時も非秘密設定だけを返す', () async {
      final publicStore = _MemoryValueStore();
      final secretStore = _MemoryValueStore();
      final repository = DirectSettingsStore(
        publicStore: publicStore,
        secretStore: secretStore,
        revisionFactory: () => 'revision-a',
      );
      await repository.save(
        const DirectSettings(
          reasoningMode: ReasoningMode.medium,
          claudeApiKey: 'secret',
          claudeModelOverride: 'model',
        ),
      );
      secretStore.failReads = true;

      final loaded = await repository.load();
      expect(loaded.reasoningMode, ReasoningMode.medium);
      expect(loaded.claudeModelOverride, 'model');
      expect(loaded.hasAnyKey, isFalse);
    });

    test('Direct無効platformではsecretを読まず旧recordを削除して公開設定だけを保存する', () async {
      final publicStore = _MemoryValueStore();
      final secretStore = _MemoryValueStore();
      final nativeRepository = DirectSettingsStore(
        publicStore: publicStore,
        secretStore: secretStore,
        revisionFactory: () => 'native-revision',
        allowDirectByok: true,
      );
      await nativeRepository.save(
        const DirectSettings(
          claudeApiKey: 'existing-secret',
          chatGptApiKey: 'another-secret',
        ),
      );
      expect(secretStore.values, isNotEmpty);

      final webRepository = DirectSettingsStore(
        publicStore: publicStore,
        secretStore: secretStore,
        revisionFactory: () => 'web-revision',
        allowDirectByok: false,
      );
      final loaded = await webRepository.load();
      expect(loaded.executionMode, ExecutionMode.referenceServer);
      expect(loaded.hasAnyKey, isFalse);
      expect(secretStore.values, isEmpty);

      await webRepository.save(
        const DirectSettings(
          executionMode: ExecutionMode.directByok,
          claudeApiKey: 'must-never-be-written',
          geminiApiKey: 'must-also-be-removed',
        ),
      );

      expect(secretStore.values, isEmpty);
      expect(publicStore.values.values.single, isNot(contains('must-never')));
      final saved = await webRepository.load();
      expect(saved.executionMode, ExecutionMode.referenceServer);
      expect(saved.hasAnyKey, isFalse);
    });

    test('revision不一致や不明な設定値をfail-closedで処理する', () async {
      final publicStore = _MemoryValueStore()
        ..values['direct_settings_public_v1'] = '''
          {"version":1,"revision":"public-revision",
            "execution_mode":"unknown","reasoning_mode":"unknown",
            "show_token_usage_ledger":"not-a-bool",
            "show_live_api_confirmation":"not-a-bool",
            "synthesizer_provider":"unknown"}
        ''';
      final secretStore = _MemoryValueStore()
        ..values['direct_settings_secret_v1'] = '''
          {"version":1,"revision":"secret-revision",
           "api_keys":{"claude":"must-not-load"}}
        ''';
      final repository = DirectSettingsStore(
        publicStore: publicStore,
        secretStore: secretStore,
      );

      final loaded = await repository.load();
      expect(loaded.executionMode, ExecutionMode.directByok);
      expect(loaded.reasoningMode, ReasoningMode.auto);
      expect(loaded.showTokenUsageLedger, isTrue);
      expect(loaded.showLiveApiConfirmation, isTrue);
      expect(loaded.synthesizerProvider, isNull);
      expect(loaded.hasAnyKey, isFalse);
    });

    test('全キー消去は秘密recordを物理削除し非秘密設定を保持する', () async {
      final publicStore = _MemoryValueStore();
      final secretStore = _MemoryValueStore();
      final revisions = ['revision-a', 'revision-b'].iterator;
      final repository = DirectSettingsStore(
        publicStore: publicStore,
        secretStore: secretStore,
        revisionFactory: () {
          revisions.moveNext();
          return revisions.current;
        },
      );
      await repository.save(
        const DirectSettings(
          executionMode: ExecutionMode.referenceServer,
          reasoningMode: ReasoningMode.high,
          showTokenUsageLedger: false,
          showLiveApiConfirmation: false,
          claudeApiKey: 'claude-secret',
          grokApiKey: 'grok-secret',
          geminiModelOverride: 'gemini-model',
          synthesizerProvider: DirectProvider.grok,
        ),
      );

      await repository.clearAllApiKeys();

      expect(secretStore.values, isEmpty);
      final loaded = await repository.load();
      expect(loaded.executionMode, ExecutionMode.referenceServer);
      expect(loaded.reasoningMode, ReasoningMode.high);
      expect(loaded.showTokenUsageLedger, isFalse);
      expect(loaded.showLiveApiConfirmation, isFalse);
      expect(loaded.geminiModelOverride, 'gemini-model');
      expect(loaded.synthesizerProvider, DirectProvider.grok);
      expect(loaded.hasAnyKey, isFalse);
      expect(publicStore.values.values.single, isNot(contains('secret')));
    });

    test('秘密record削除失敗では公開revisionを変更しない', () async {
      final publicStore = _MemoryValueStore();
      final secretStore = _MemoryValueStore();
      final revisions = ['revision-a', 'revision-b'].iterator;
      final repository = DirectSettingsStore(
        publicStore: publicStore,
        secretStore: secretStore,
        revisionFactory: () {
          revisions.moveNext();
          return revisions.current;
        },
      );
      await repository.save(
        const DirectSettings(claudeApiKey: 'existing-secret'),
      );
      final publicBefore = publicStore.values.values.single;
      secretStore.failNextDelete = true;

      await expectLater(repository.clearAllKeys(), throwsA(isA<StateError>()));

      expect(publicStore.values.values.single, publicBefore);
      expect((await repository.load()).claudeApiKey, 'existing-secret');
    });

    test('空revisionではどちらのstoreも変更しない', () async {
      final publicStore = _MemoryValueStore();
      final secretStore = _MemoryValueStore();
      final repository = DirectSettingsStore(
        publicStore: publicStore,
        secretStore: secretStore,
        revisionFactory: () => '   ',
      );

      await expectLater(
        repository.save(const DirectSettings(claudeApiKey: 'secret')),
        throwsA(isA<StateError>()),
      );
      expect(publicStore.values, isEmpty);
      expect(secretStore.values, isEmpty);
    });

    test('同時saveを直列化し公開revisionと秘密revisionを混在させない', () async {
      final tracker = _OperationTracker();
      final publicStore = _TrackedValueStore(tracker);
      final secretStore = _TrackedValueStore(tracker);
      final revisions = ['revision-a', 'revision-b'].iterator;
      final repository = DirectSettingsStore(
        publicStore: publicStore,
        secretStore: secretStore,
        revisionFactory: () {
          revisions.moveNext();
          return revisions.current;
        },
      );

      await Future.wait([
        repository.save(
          const DirectSettings(
            executionMode: ExecutionMode.directByok,
            claudeApiKey: 'first-secret',
          ),
        ),
        repository.save(
          const DirectSettings(
            executionMode: ExecutionMode.referenceServer,
            claudeApiKey: 'second-secret',
          ),
        ),
      ]);

      expect(tracker.overlapDetected, isFalse);
      final loaded = await repository.load();
      expect(loaded.executionMode, ExecutionMode.referenceServer);
      expect(loaded.claudeApiKey, 'second-secret');
      expect(publicStore.values.values.single, contains('revision-b'));
      expect(secretStore.values.values.single, contains('revision-b'));
    });
  });
}

class _MemoryValueStore implements DirectSettingsValueStore {
  final Map<String, String> values = {};
  bool failReads = false;
  bool failNextWrite = false;
  bool failNextDelete = false;

  @override
  Future<String?> read(String key) async {
    if (failReads) throw StateError('simulated read failure');
    return values[key];
  }

  @override
  Future<void> write(String key, String value) async {
    if (failNextWrite) {
      failNextWrite = false;
      throw StateError('simulated write failure');
    }
    values[key] = value;
  }

  @override
  Future<void> delete(String key) async {
    if (failNextDelete) {
      failNextDelete = false;
      throw StateError('simulated delete failure');
    }
    values.remove(key);
  }
}

class _OperationTracker {
  var active = 0;
  var overlapDetected = false;
}

class _TrackedValueStore implements DirectSettingsValueStore {
  _TrackedValueStore(this.tracker);

  final _OperationTracker tracker;
  final Map<String, String> values = {};

  Future<T> _track<T>(T Function() action) async {
    tracker.active++;
    if (tracker.active > 1) tracker.overlapDetected = true;
    try {
      await Future<void>.delayed(const Duration(milliseconds: 2));
      return action();
    } finally {
      tracker.active--;
    }
  }

  @override
  Future<void> delete(String key) => _track(() => values.remove(key));

  @override
  Future<String?> read(String key) => _track(() => values[key]);

  @override
  Future<void> write(String key, String value) =>
      _track(() => values[key] = value);
}
