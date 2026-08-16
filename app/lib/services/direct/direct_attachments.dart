// Direct BYOKの添付snapshot。本文は端末メモリだけに置き、SharedPreferencesへ
// 平文で残さない。そのためアプリを再起動すると本文は失われる。
part of '../direct_byok_client.dart';

// 1件あたりと合計を同じ値にしているのは、添付本文を端末メモリだけに置く
// 初期版の方針から。合計を先に緩める場合でも1件あたりの上限は残す。
const _maxDirectAttachmentBytes = 512 * 1024;
const _maxDirectAttachmentTotalBytes = 512 * 1024;
const _maxDirectAttachments = 8;

String _attachmentContext(List<_DirectAttachment> attachments) {
  final blocks = <String>[];
  for (final item in attachments) {
    blocks.add('\n\n[添付: ${item.name}]\n${item.text}');
  }
  return blocks.join();
}

List<Map<String, dynamic>> _attachmentReferences(
  String conversationId,
  List<_DirectAttachment> attachments,
) => [
  for (final attachment in attachments)
    attachment.record(conversationId).letJson(),
];

extension _DirectAttachments on DirectByokClient {
  List<_DirectAttachment> _requiredAttachments(
    String conversationId,
    List<String> ids,
  ) =>
      _attachmentSnapshotOrNull(conversationId, ids) ??
      (throw const ApiException('添付が見つからないため、実APIへ送信しません。'));

  /// 添付snapshotを取り出す。1件でも欠けていればnull。
  ///
  /// 添付本文は端末メモリだけに置き、SharedPreferencesへ平文で残さない。
  /// このためアプリを再起動すると本文は失われ、IDだけが保存turnに残る。
  /// 計画経路はこのnullを「送れない理由」として説明でき、実行経路は
  /// [_requiredAttachments] のまま例外でfail-closedを保つ。
  List<_DirectAttachment>? _attachmentSnapshotOrNull(
    String conversationId,
    List<String> ids,
  ) {
    final items = _attachments[conversationId] ?? const {};
    final result = <_DirectAttachment>[];
    final seen = <String>{};
    for (final rawId in ids) {
      final id = rawId.trim();
      if (id.isEmpty || !seen.add(id)) continue;
      final item = items[id];
      if (item == null) return null;
      result.add(item);
    }
    // uploadAttachment側でも同じ上限を確認しているが、保存済みIDから作った
    // snapshotを実APIへ渡す直前にもう一度確認する(送信経路のfail-closed)。
    if (result.length > _maxDirectAttachments ||
        result.fold<int>(
              0,
              (total, attachment) => total + attachment.sizeBytes,
            ) >
            _maxDirectAttachmentTotalBytes ||
        result.any(
          (attachment) =>
              attachment.sizeBytes > _maxDirectAttachmentBytes ||
              attachment.name.contains('\u0000') ||
              attachment.text.contains('\u0000'),
        )) {
      throw const ApiException('添付snapshotがDirect BYOKの安全上限を超えています。');
    }
    return List.unmodifiable(result);
  }
}

class _DirectAttachment {
  const _DirectAttachment({
    required this.id,
    required this.name,
    required this.text,
    required this.sizeBytes,
    required this.createdAt,
  });

  final String id;
  final String name;
  final String text;
  final int sizeBytes;
  final String createdAt;

  AttachmentRecord record(String conversationId) => AttachmentRecord.fromJson({
    'id': id,
    'conversation_id': conversationId,
    'name': name,
    'mime_type': 'text/plain; charset=utf-8',
    'kind': 'text',
    'size_bytes': sizeBytes,
    'created_at': createdAt,
    'expires_at': '',
    'text_extractable': true,
    'included_in_prompt': true,
    'truncated': false,
  });
}

extension on AttachmentRecord {
  Map<String, dynamic> letJson() => {
    'id': id,
    'conversation_id': conversationId,
    'name': name,
    'mime_type': mimeType,
    'kind': kind,
    'size_bytes': sizeBytes,
    'created_at': createdAt,
    'expires_at': expiresAt,
    'text_extractable': textExtractable,
    'included_in_prompt': includedInPrompt,
    'truncated': truncated,
  };
}
