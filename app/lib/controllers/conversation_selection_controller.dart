import '../models.dart';

class ConversationSelectionToken {
  const ConversationSelectionToken._(this.generation, this.selectedId);

  final int generation;
  final String? selectedId;
}

/// Owns conversation selection identity and guards asynchronous commits.
///
/// UI code may start several kinds of work (refresh, selection, upload, fork),
/// but only the latest token for the still-selected conversation may commit.
class ConversationSelectionController {
  int _generation = 0;
  String? _selectedId;
  ConversationRecord? _conversation;
  bool _loading = false;

  String? get selectedId => _selectedId;
  ConversationRecord? get conversation => _conversation;
  bool get loading => _loading;
  int get generation => _generation;

  ConversationSelectionToken beginSelection(String id) {
    _generation++;
    if (_selectedId != id) _conversation = null;
    _selectedId = id;
    _loading = true;
    return _token();
  }

  ConversationSelectionToken beginOperation({bool loading = false}) {
    _generation++;
    _loading = loading;
    return _token();
  }

  bool isCurrent(ConversationSelectionToken token) =>
      token.generation == _generation && token.selectedId == _selectedId;

  bool commit(
    ConversationSelectionToken token, {
    required ConversationRecord? conversation,
    bool selectConversation = false,
    bool finishLoading = true,
  }) {
    if (!isCurrent(token)) return false;
    if (selectConversation && conversation != null) {
      _selectedId = conversation.id;
    }
    _conversation = conversation;
    if (finishLoading) _loading = false;
    _generation++;
    return true;
  }

  bool finish(ConversationSelectionToken token) {
    if (!isCurrent(token)) return false;
    _loading = false;
    _generation++;
    return true;
  }

  void restore({String? selectedId, ConversationRecord? conversation}) {
    _generation++;
    _selectedId = selectedId;
    _conversation = conversation;
    _loading = false;
  }

  void selectId(String? id) {
    _generation++;
    if (_selectedId != id) _conversation = null;
    _selectedId = id;
    _loading = false;
  }

  bool replaceConversationIfSelected(ConversationRecord conversation) {
    if (_selectedId != conversation.id) return false;
    _conversation = conversation;
    _loading = false;
    _generation++;
    return true;
  }

  void clear() => restore();

  void invalidate() {
    _generation++;
    _loading = false;
  }

  ConversationSelectionToken _token() =>
      ConversationSelectionToken._(_generation, _selectedId);
}
