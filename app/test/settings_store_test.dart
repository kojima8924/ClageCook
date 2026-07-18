import 'package:clage_cook/models.dart';
import 'package:clage_cook/services/settings_store.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  test('URLとBearer tokenを同じrevisionへ結び付けて復元する', () async {
    final publicStore = _MemoryValueStore();
    final secretStore = _MemoryValueStore();
    final repository = SettingsStore(
      publicStore: publicStore,
      secretStore: secretStore,
      revisionFactory: () => 'revision-a',
    );

    await repository.save(
      const ConnectionSettings(
        baseUrl: 'https://clage.example.test/',
        token: 'token-for-a',
      ),
    );

    expect(await repository.load(), isA<ConnectionSettings>());
    final loaded = await repository.load();
    expect(loaded.baseUrl, 'https://clage.example.test');
    expect(loaded.token, 'token-for-a');
    expect(publicStore.values.values.single, isNot(contains('token-for-a')));
    expect(secretStore.values.values.single, contains('token-for-a'));
  });

  test('秘密側write失敗では公開接続先を変更しない', () async {
    final publicStore = _MemoryValueStore();
    final secretStore = _MemoryValueStore();
    final revisions = ['revision-a', 'revision-b'].iterator;
    final repository = SettingsStore(
      publicStore: publicStore,
      secretStore: secretStore,
      revisionFactory: () {
        revisions.moveNext();
        return revisions.current;
      },
    );
    await repository.save(
      const ConnectionSettings(
        baseUrl: 'https://a.example.test',
        token: 'token-for-a',
      ),
    );
    secretStore.failNextWrite = true;

    await expectLater(
      repository.save(
        const ConnectionSettings(
          baseUrl: 'https://b.example.test',
          token: 'token-for-b',
        ),
      ),
      throwsA(isA<StateError>()),
    );

    final loaded = await repository.load();
    expect(loaded.baseUrl, 'https://a.example.test');
    expect(loaded.token, 'token-for-a');
  });

  test('公開側write失敗後は旧URLへ新tokenも旧tokenも送らない', () async {
    final publicStore = _MemoryValueStore();
    final secretStore = _MemoryValueStore();
    final revisions = ['revision-a', 'revision-b'].iterator;
    final repository = SettingsStore(
      publicStore: publicStore,
      secretStore: secretStore,
      revisionFactory: () {
        revisions.moveNext();
        return revisions.current;
      },
    );
    await repository.save(
      const ConnectionSettings(
        baseUrl: 'https://a.example.test',
        token: 'token-for-a',
      ),
    );
    publicStore.failNextWrite = true;

    await expectLater(
      repository.save(
        const ConnectionSettings(
          baseUrl: 'https://b.example.test',
          token: 'token-for-b',
        ),
      ),
      throwsA(isA<StateError>()),
    );

    final loaded = await repository.load();
    expect(loaded.baseUrl, 'https://a.example.test');
    expect(loaded.token, isEmpty);
  });

  test('公開recordと秘密recordのbase URLまたはoriginが違えばtokenを破棄する', () async {
    final publicStore = _MemoryValueStore();
    final secretStore = _MemoryValueStore();
    final repository = SettingsStore(
      publicStore: publicStore,
      secretStore: secretStore,
      revisionFactory: () => 'revision-a',
    );
    await repository.save(
      const ConnectionSettings(
        baseUrl: 'https://a.example.test',
        token: 'token-for-a',
      ),
    );
    final secretKey = secretStore.values.keys.single;
    secretStore.values[secretKey] = secretStore.values[secretKey]!.replaceFirst(
      'https://a.example.test',
      'https://b.example.test',
    );

    final loaded = await repository.load();
    expect(loaded.baseUrl, 'https://a.example.test');
    expect(loaded.token, isEmpty);
  });

  test('旧形式の平文URLやtoken keyを移行せず安全な既定値へ戻す', () async {
    final publicStore = _MemoryValueStore()
      ..values['server_base_url'] = 'https://legacy.example.test';
    final secretStore = _MemoryValueStore()
      ..values['server_bearer_token'] = 'legacy-token';
    final repository = SettingsStore(
      publicStore: publicStore,
      secretStore: secretStore,
      revisionFactory: () => 'unused',
    );

    final loaded = await repository.load();
    expect(loaded.baseUrl, 'http://127.0.0.1:8000');
    expect(loaded.token, isEmpty);
  });

  test('reverse proxy pathだけを許可しuserinfo query fragmentを拒否する', () async {
    expect(validateServerBaseUrl('https://example.test/clage'), isNull);
    expect(validateServerBaseUrl('http://100.92.38.91:8000/base/'), isNull);
    expect(
      validateServerBaseUrl('https://user:secret@example.test/clage'),
      contains('ユーザー名やパスワード'),
    );
    expect(
      validateServerBaseUrl('https://example.test/clage?token=secret'),
      contains('クエリ'),
    );
    expect(
      validateServerBaseUrl('https://example.test/clage#secret'),
      contains('フラグメント'),
    );
  });

  test('不正URLは公開・秘密storeのどちらにも保存しない', () async {
    final publicStore = _MemoryValueStore();
    final secretStore = _MemoryValueStore();
    final repository = SettingsStore(
      publicStore: publicStore,
      secretStore: secretStore,
      revisionFactory: () => 'unused',
    );

    await expectLater(
      repository.save(
        const ConnectionSettings(
          baseUrl: 'https://user:secret@example.test/?token=secret',
          token: 'bearer-secret',
        ),
      ),
      throwsA(isA<StateError>()),
    );
    expect(publicStore.values, isEmpty);
    expect(secretStore.values, isEmpty);
  });
}

class _MemoryValueStore implements SettingsValueStore {
  final Map<String, String> values = {};
  bool failNextWrite = false;

  @override
  Future<String?> read(String key) async => values[key];

  @override
  Future<void> write(String key, String value) async {
    if (failNextWrite) {
      failNextWrite = false;
      throw StateError('simulated write failure');
    }
    values[key] = value;
  }
}
