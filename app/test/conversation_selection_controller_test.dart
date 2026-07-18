import 'package:clage_cook/controllers/conversation_selection_controller.dart';
import 'package:clage_cook/models.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  test('選択変更後は古い会話取得をcommitできない', () {
    final controller = ConversationSelectionController()
      ..restore(selectedId: 'a', conversation: _conversation('a', '会話A'));

    final staleRefresh = controller.beginOperation();
    final selection = controller.beginSelection('b');

    expect(
      controller.commit(
        staleRefresh,
        conversation: _conversation('a', '古い会話A'),
      ),
      isFalse,
    );
    expect(controller.selectedId, 'b');
    expect(controller.loading, isTrue);

    expect(
      controller.commit(selection, conversation: _conversation('b', '会話B')),
      isTrue,
    );
    expect(controller.conversation?.title, '会話B');
    expect(controller.loading, isFalse);
  });

  test('別会話の読込中は前の会話を選択中として保持しない', () {
    final controller = ConversationSelectionController()
      ..restore(selectedId: 'a', conversation: _conversation('a', '会話A'));

    controller.beginSelection('b');

    expect(controller.selectedId, 'b');
    expect(controller.conversation, isNull);
    expect(controller.loading, isTrue);
  });

  test('添付先を変更した後は古いupload結果を反映しない', () {
    final controller = ConversationSelectionController();
    final upload = controller.beginOperation();

    controller.selectId('other');

    expect(
      controller.commit(
        upload,
        conversation: _conversation('draft', '下書き'),
        selectConversation: true,
      ),
      isFalse,
    );
    expect(controller.selectedId, 'other');
    expect(controller.conversation, isNull);
  });

  test('現在のupload結果は作成した会話を選択できる', () {
    final controller = ConversationSelectionController();
    final upload = controller.beginOperation();
    final draft = _conversation('draft', '下書き');

    expect(
      controller.commit(upload, conversation: draft, selectConversation: true),
      isTrue,
    );
    expect(controller.selectedId, 'draft');
    expect(controller.conversation, same(draft));
  });

  test('選択中会話の置換は世代を進めて古い処理を無効化する', () {
    final controller = ConversationSelectionController()
      ..restore(selectedId: 'a', conversation: _conversation('a', '会話A'));
    final pending = controller.beginOperation(loading: true);

    expect(
      controller.replaceConversationIfSelected(_conversation('a', '更新済み')),
      isTrue,
    );
    expect(controller.isCurrent(pending), isFalse);
    expect(controller.conversation?.title, '更新済み');
    expect(controller.loading, isFalse);
  });
}

ConversationRecord _conversation(String id, String title) =>
    ConversationRecord(id: id, title: title, turns: const []);
