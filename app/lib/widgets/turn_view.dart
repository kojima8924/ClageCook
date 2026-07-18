import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_markdown_plus/flutter_markdown_plus.dart';
import 'package:url_launcher/url_launcher.dart';

import '../models.dart';
import 'insights_panel.dart';

/// 保存済みの1ターンを、ユーザー発言・各Provider回答・統合回答の順で表示する。
class SavedTurnView extends StatelessWidget {
  const SavedTurnView({
    super.key,
    required this.turn,
    this.onReconnect,
    this.onCancel,
    this.onRegenerateAnswer,
    this.onRegenerateSynthesis,
    this.onForkEdit,
    this.actionPending = false,
    this.regenerationPending = false,
  });

  final TurnRecord turn;
  final VoidCallback? onReconnect;
  final VoidCallback? onCancel;
  final ValueChanged<String>? onRegenerateAnswer;
  final VoidCallback? onRegenerateSynthesis;
  final VoidCallback? onForkEdit;
  final bool actionPending;
  final bool regenerationPending;

  @override
  Widget build(BuildContext context) {
    final providerNames = <String>{...turn.providers, ...turn.answers.keys};
    return _TurnView(
      message: turn.message.isNotEmpty ? turn.message : turn.cleanMessage,
      answers: turn.answers,
      providerNames: providerNames,
      synthesis: turn.synthesis,
      insights: turn.insights,
      liveError: _savedTurnNotice(turn),
      attempts: turn.attempts,
      synthesisStale: turn.synthesisStale,
      regenerationPending: regenerationPending,
      onRegenerateAnswer: turn.status == 'completed'
          ? onRegenerateAnswer
          : null,
      onRegenerateSynthesis: turn.status == 'completed'
          ? onRegenerateSynthesis
          : null,
      onForkEdit: turn.status == 'completed' ? onForkEdit : null,
      attachments: turn.attachments,
      statusActions: turn.status == 'running'
          ? Wrap(
              spacing: 8,
              runSpacing: 8,
              children: [
                OutlinedButton.icon(
                  onPressed: actionPending ? null : onReconnect,
                  icon: const Icon(Icons.sync),
                  label: const Text('実行へ再接続'),
                ),
                FilledButton.tonalIcon(
                  onPressed: actionPending ? null : onCancel,
                  icon: actionPending
                      ? const SizedBox.square(
                          dimension: 16,
                          child: CircularProgressIndicator(strokeWidth: 2),
                        )
                      : const Icon(Icons.stop),
                  label: Text(actionPending ? '状態を確認中' : '停止を要求'),
                ),
              ],
            )
          : null,
    );
  }
}

/// SSE受信中の1ターンを表示する。
///
/// [LiveTurn] 自体はmutableなので、呼び出し側でイベント受信時に再buildする。
class LiveTurnView extends StatelessWidget {
  const LiveTurnView({super.key, required this.turn});

  final LiveTurn turn;

  @override
  Widget build(BuildContext context) {
    final names = <String>{...turn.providers, ...turn.answers.keys};
    return _TurnView(
      message: turn.message,
      answers: turn.answers,
      providerNames: names.isEmpty ? _providerOrder : names,
      synthesis: turn.synthesis,
      insights: turn.insights,
      livePhase: turn.phase,
      liveError: turn.error,
      synthesisPending: turn.synthesis == null && turn.error.isEmpty,
    );
  }
}

const _providerOrder = ['claude', 'gemini', 'chatgpt', 'grok'];

const _providerLabels = {
  'claude': 'Claude',
  'gemini': 'Gemini',
  'chatgpt': 'ChatGPT',
  'grok': 'Grok',
};

const _providerColors = {
  'claude': Color(0xFFD97757),
  'gemini': Color(0xFF4285F4),
  'chatgpt': Color(0xFF10A37F),
  'grok': Color(0xFF8B95A5),
};

class _TurnView extends StatelessWidget {
  const _TurnView({
    required this.message,
    required this.answers,
    required this.providerNames,
    required this.synthesis,
    this.insights,
    this.livePhase = '',
    this.liveError = '',
    this.synthesisPending = false,
    this.statusActions,
    this.attempts = const [],
    this.synthesisStale = false,
    this.regenerationPending = false,
    this.onRegenerateAnswer,
    this.onRegenerateSynthesis,
    this.onForkEdit,
    this.attachments = const [],
  });

  final String message;
  final Map<String, AnswerRecord> answers;
  final Iterable<String> providerNames;
  final SynthesisRecord? synthesis;
  final Map<String, dynamic>? insights;
  final String livePhase;
  final String liveError;
  final bool synthesisPending;
  final Widget? statusActions;
  final List<RegenerationAttempt> attempts;
  final bool synthesisStale;
  final bool regenerationPending;
  final ValueChanged<String>? onRegenerateAnswer;
  final VoidCallback? onRegenerateSynthesis;
  final VoidCallback? onForkEdit;
  final List<AttachmentRecord> attachments;

  @override
  Widget build(BuildContext context) {
    final providers = _orderedProviders(providerNames);
    final usageEntries = <Map<String, dynamic>>[
      for (final entry in answers.entries)
        if (entry.value.usage.isNotEmpty)
          {
            'source': _providerLabels[entry.key] ?? entry.key,
            'model': entry.value.model,
            'phase': entry.value.round > 1 ? '回答 + DEBATE' : '回答',
            'usage': entry.value.usage,
          },
      if (synthesis != null && synthesis!.usage.isNotEmpty)
        {
          'source':
              '統合 · ${_providerLabels[synthesis!.source] ?? synthesis!.source}',
          'model': synthesis!.model,
          'phase': '統合',
          'usage': synthesis!.usage,
        },
    ];
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        _UserMessage(message: message, onForkEdit: onForkEdit),
        if (attachments.isNotEmpty) ...[
          const SizedBox(height: 6),
          _AttachmentChips(attachments: attachments),
        ],
        if (livePhase.isNotEmpty || liveError.isNotEmpty) ...[
          const SizedBox(height: 8),
          _LiveStatus(phase: livePhase, error: liveError),
        ],
        if (statusActions != null) ...[
          const SizedBox(height: 8),
          statusActions!,
        ],
        const SizedBox(height: 8),
        for (final provider in providers) ...[
          _AnswerCard(
            provider: provider,
            answer: answers[provider],
            pending: answers[provider] == null && liveError.isEmpty,
            regenerationPending: regenerationPending,
            onRegenerate: onRegenerateAnswer == null
                ? null
                : () => onRegenerateAnswer!(provider),
          ),
          const SizedBox(height: 8),
        ],
        if (insights?.isNotEmpty == true || usageEntries.isNotEmpty) ...[
          InsightsPanel(insights: insights, usageEntries: usageEntries),
          const SizedBox(height: 8),
        ],
        if (attempts.isNotEmpty) ...[
          _RevisionSummary(attempts: attempts),
          const SizedBox(height: 8),
        ],
        if (synthesisStale) ...[
          const _ErrorBox(
            title: '統合回答は更新前です',
            message:
                '個別回答を再生成したため、統合回答は現在の回答集合と一致しない可能性があります。必要なら統合も再生成してください。',
            warning: true,
          ),
          const SizedBox(height: 8),
        ],
        if (synthesis != null || synthesisPending)
          _SynthesisCard(
            synthesis: synthesis,
            pending: synthesisPending,
            regenerationPending: regenerationPending,
            onRegenerate: onRegenerateSynthesis,
          ),
      ],
    );
  }
}

class _AttachmentChips extends StatelessWidget {
  const _AttachmentChips({required this.attachments});

  final List<AttachmentRecord> attachments;

  @override
  Widget build(BuildContext context) => Align(
    alignment: Alignment.centerRight,
    child: Wrap(
      spacing: 6,
      runSpacing: 6,
      alignment: WrapAlignment.end,
      children: [
        for (final attachment in attachments)
          Tooltip(
            message:
                '${attachment.mimeType} · ${_attachmentBytes(attachment.sizeBytes)}'
                '${attachment.includedInPrompt ? ' · promptへ取込済み' : ' · binary保存のみ'}',
            child: Chip(
              avatar: Icon(
                attachment.kind == 'image'
                    ? Icons.image_outlined
                    : attachment.kind == 'pdf'
                    ? Icons.picture_as_pdf_outlined
                    : Icons.description_outlined,
                size: 17,
              ),
              label: Text(attachment.name),
            ),
          ),
      ],
    ),
  );
}

String _attachmentBytes(int bytes) {
  if (bytes < 1024) return '$bytes B';
  final kib = bytes / 1024;
  if (kib < 1024) return '${kib.toStringAsFixed(kib >= 100 ? 0 : 1)} KiB';
  final mib = kib / 1024;
  return '${mib.toStringAsFixed(mib >= 100 ? 0 : 2)} MiB';
}

class _UserMessage extends StatelessWidget {
  const _UserMessage({required this.message, this.onForkEdit});

  final String message;
  final VoidCallback? onForkEdit;

  @override
  Widget build(BuildContext context) {
    final colors = Theme.of(context).colorScheme;
    return Align(
      alignment: Alignment.centerRight,
      child: ConstrainedBox(
        constraints: const BoxConstraints(maxWidth: 720),
        child: Card(
          color: colors.primaryContainer,
          margin: EdgeInsets.zero,
          child: Padding(
            padding: const EdgeInsets.all(14),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(
                  children: [
                    Expanded(
                      child: Text(
                        'あなた',
                        style: Theme.of(context).textTheme.labelLarge?.copyWith(
                          color: colors.onPrimaryContainer,
                          fontWeight: FontWeight.bold,
                        ),
                      ),
                    ),
                    if (onForkEdit != null)
                      IconButton(
                        tooltip: 'この発言を編集して分岐',
                        visualDensity: VisualDensity.compact,
                        onPressed: onForkEdit,
                        icon: const Icon(Icons.call_split, size: 19),
                      ),
                  ],
                ),
                const SizedBox(height: 5),
                SelectableText(
                  message.isEmpty ? '（空のメッセージ）' : message,
                  style: TextStyle(color: colors.onPrimaryContainer),
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }
}

class _LiveStatus extends StatelessWidget {
  const _LiveStatus({required this.phase, required this.error});

  final String phase;
  final String error;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final failed = error.isNotEmpty;
    final colors = theme.colorScheme;
    return Semantics(
      liveRegion: true,
      label: failed ? error : phase,
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
        decoration: BoxDecoration(
          color: failed ? colors.errorContainer : colors.surfaceContainerHigh,
          borderRadius: BorderRadius.circular(12),
        ),
        child: Row(
          children: [
            if (failed)
              Icon(Icons.error_outline, color: colors.onErrorContainer)
            else
              const SizedBox.square(
                dimension: 18,
                child: CircularProgressIndicator(strokeWidth: 2),
              ),
            const SizedBox(width: 10),
            Expanded(
              child: SelectableText(
                failed ? error : phase,
                style: theme.textTheme.bodyMedium?.copyWith(
                  color: failed ? colors.onErrorContainer : null,
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class _AnswerCard extends StatelessWidget {
  const _AnswerCard({
    required this.provider,
    required this.answer,
    required this.pending,
    this.regenerationPending = false,
    this.onRegenerate,
  });

  final String provider;
  final AnswerRecord? answer;
  final bool pending;
  final bool regenerationPending;
  final VoidCallback? onRegenerate;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final colors = theme.colorScheme;
    final accent = _providerColors[provider] ?? colors.secondary;
    final current = answer;
    final failed = current != null && !current.ok;
    final hasInitial = current?.round1Text.trim().isNotEmpty == true;
    final hasDebateError = current?.debateError.trim().isNotEmpty == true;

    return Card(
      margin: EdgeInsets.zero,
      clipBehavior: Clip.antiAlias,
      child: ExpansionTile(
        maintainState: true,
        initiallyExpanded: failed || hasDebateError || current?.partial == true,
        tilePadding: const EdgeInsets.symmetric(horizontal: 14, vertical: 2),
        childrenPadding: const EdgeInsets.fromLTRB(14, 0, 14, 14),
        leading: Container(
          width: 4,
          height: 28,
          decoration: BoxDecoration(
            color: accent,
            borderRadius: BorderRadius.circular(2),
          ),
        ),
        title: Row(
          children: [
            Expanded(
              child: Text(
                _providerLabels[provider] ?? provider,
                style: theme.textTheme.titleMedium?.copyWith(
                  color: accent,
                  fontWeight: FontWeight.bold,
                ),
              ),
            ),
            if (pending)
              const SizedBox.square(
                dimension: 18,
                child: CircularProgressIndicator(strokeWidth: 2),
              )
            else ...[
              if (onRegenerate != null && current != null)
                IconButton(
                  tooltip: '${_providerLabels[provider] ?? provider}の回答を再生成',
                  onPressed: regenerationPending ? null : onRegenerate,
                  icon: regenerationPending
                      ? const SizedBox.square(
                          dimension: 16,
                          child: CircularProgressIndicator(strokeWidth: 2),
                        )
                      : const Icon(Icons.replay_outlined),
                ),
              if (failed)
                Icon(Icons.error_outline, color: colors.error)
              else if (current?.partial == true ||
                  current?.usageMayBeIncomplete == true)
                Icon(Icons.warning_amber_rounded, color: colors.error)
              else
                Icon(Icons.check_circle_outline, color: accent),
            ],
          ],
        ),
        subtitle: current == null
            ? Text(pending ? '回答待ち…' : '回答なし', style: theme.textTheme.bodySmall)
            : Padding(
                padding: const EdgeInsets.only(top: 6),
                child: _Metadata(
                  model: current.model,
                  elapsedSec: current.elapsedSec,
                  mock: current.mock,
                  debateRound: current.round,
                  partial: current.partial,
                  incompleteReason: current.incompleteReason,
                  usageMayBeIncomplete: current.usageMayBeIncomplete,
                  requestAudit: current.requestAudit,
                  webSearchRequested: current.webSearchRequested,
                ),
              ),
        children: [
          if (current == null)
            Align(
              alignment: Alignment.centerLeft,
              child: Text(
                pending ? '回答を生成しています…' : 'このProviderの回答はありません。',
                style: theme.textTheme.bodyMedium?.copyWith(
                  color: colors.onSurfaceVariant,
                ),
              ),
            )
          else ...[
            if (current.partial) ...[
              _ErrorBox(
                title: '部分回答',
                message: _partialMessage(current.incompleteReason),
                warning: true,
              ),
              const SizedBox(height: 10),
            ],
            if (!current.ok)
              _ErrorBox(
                message: current.error.isEmpty
                    ? '回答の生成に失敗しました。'
                    : current.error,
              ),
            if (current.text.trim().isNotEmpty) ...[
              if (!current.ok) const SizedBox(height: 10),
              _Markdown(text: current.text),
              if (current.citations.isNotEmpty) ...[
                const SizedBox(height: 12),
                _CitationList(citations: current.citations),
              ],
            ] else if (current.ok)
              Text(
                '（回答は空です）',
                style: TextStyle(color: colors.onSurfaceVariant),
              ),
            if (hasDebateError) ...[
              const SizedBox(height: 12),
              _ErrorBox(
                title: '相互批評',
                message: current.debateError,
                warning: true,
              ),
            ],
            if (hasInitial) ...[
              const SizedBox(height: 8),
              _InitialAnswer(answer: current),
            ],
          ],
        ],
      ),
    );
  }
}

class _Metadata extends StatelessWidget {
  const _Metadata({
    required this.model,
    required this.elapsedSec,
    required this.mock,
    required this.debateRound,
    required this.partial,
    required this.incompleteReason,
    required this.usageMayBeIncomplete,
    required this.requestAudit,
    required this.webSearchRequested,
  });

  final String model;
  final double elapsedSec;
  final bool mock;
  final int debateRound;
  final bool partial;
  final String incompleteReason;
  final bool usageMayBeIncomplete;
  final Map<String, dynamic> requestAudit;
  final bool webSearchRequested;

  @override
  Widget build(BuildContext context) {
    final chips = <Widget>[
      if (model.trim().isNotEmpty &&
          !(mock && model.trim().toLowerCase() == 'mock'))
        _MetaChip(label: model.trim()),
      if (elapsedSec > 0) _MetaChip(label: _elapsedLabel(elapsedSec)),
      if (mock) const _MetaChip(label: 'MOCK', emphasized: true),
      if (debateRound > 1)
        _MetaChip(label: 'DEBATE R$debateRound', emphasized: true),
      if (webSearchRequested) const _MetaChip(label: 'WEB許可', emphasized: true),
      if (partial)
        _MetaChip(
          label: incompleteReason.isEmpty
              ? 'PARTIAL'
              : 'PARTIAL · ${_reasonLabel(incompleteReason)}',
          warning: true,
        ),
      if (_auditAttempts(requestAudit) > 1)
        _MetaChip(
          label: 'HTTP ${_auditAttempts(requestAudit)}回',
          warning: true,
        ),
      if (usageMayBeIncomplete)
        const _MetaChip(label: '利用量は不完全な可能性', warning: true),
    ];
    if (chips.isEmpty) return const SizedBox.shrink();
    return Wrap(spacing: 6, runSpacing: 5, children: chips);
  }
}

class _MetaChip extends StatelessWidget {
  const _MetaChip({
    required this.label,
    this.emphasized = false,
    this.warning = false,
  });

  final String label;
  final bool emphasized;
  final bool warning;

  @override
  Widget build(BuildContext context) {
    final colors = Theme.of(context).colorScheme;
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 7, vertical: 2),
      decoration: BoxDecoration(
        color: warning
            ? colors.errorContainer
            : emphasized
            ? colors.tertiaryContainer
            : colors.surfaceContainerHighest,
        borderRadius: BorderRadius.circular(999),
      ),
      child: Text(
        label,
        maxLines: 1,
        overflow: TextOverflow.ellipsis,
        style: Theme.of(context).textTheme.labelSmall?.copyWith(
          color: warning
              ? colors.onErrorContainer
              : emphasized
              ? colors.onTertiaryContainer
              : colors.onSurfaceVariant,
          fontWeight: emphasized || warning ? FontWeight.bold : null,
        ),
      ),
    );
  }
}

class _CitationList extends StatelessWidget {
  const _CitationList({required this.citations});

  final List<CitationRecord> citations;

  @override
  Widget build(BuildContext context) => Column(
    crossAxisAlignment: CrossAxisAlignment.start,
    children: [
      Text('出典', style: Theme.of(context).textTheme.labelLarge),
      const SizedBox(height: 6),
      Wrap(
        spacing: 6,
        runSpacing: 6,
        children: [
          for (var index = 0; index < citations.length; index++)
            ActionChip(
              avatar: const Icon(Icons.open_in_new, size: 16),
              label: Text(
                '${index + 1}. ${citations[index].title}',
                overflow: TextOverflow.ellipsis,
              ),
              tooltip: citations[index].url,
              onPressed: () => _openHttpUrl(citations[index].url),
            ),
        ],
      ),
    ],
  );
}

void _openHttpUrl(String raw) {
  final uri = Uri.tryParse(raw);
  if (uri == null || !{'http', 'https'}.contains(uri.scheme)) return;
  unawaited(launchUrl(uri, mode: LaunchMode.externalApplication));
}

class _InitialAnswer extends StatelessWidget {
  const _InitialAnswer({required this.answer});

  final AnswerRecord answer;

  @override
  Widget build(BuildContext context) {
    final usage = answer.round1Usage.isEmpty
        ? const <String, int>{}
        : answer.round1Usage;
    final model = answer.round1Model.isEmpty
        ? (answer.debateError.isNotEmpty ? answer.model : '')
        : answer.round1Model;
    final attempts = _auditAttempts(answer.round1RequestAudit);
    return ExpansionTile(
      tilePadding: EdgeInsets.zero,
      childrenPadding: const EdgeInsets.only(bottom: 8),
      title: const Text('最初の回答（批評前）'),
      leading: const Icon(Icons.history),
      children: [
        if (model.isNotEmpty ||
            answer.round1ElapsedSec > 0 ||
            usage.isNotEmpty ||
            attempts > 1 ||
            answer.round1Partial)
          Padding(
            padding: const EdgeInsets.only(bottom: 8),
            child: Wrap(
              spacing: 6,
              runSpacing: 5,
              children: [
                if (model.isNotEmpty) _MetaChip(label: model),
                if (answer.round1ElapsedSec > 0)
                  _MetaChip(label: _elapsedLabel(answer.round1ElapsedSec)),
                if (usage['total_tokens'] != null)
                  _MetaChip(label: '初回 ${usage['total_tokens']} token'),
                if (attempts > 1)
                  _MetaChip(label: 'HTTP $attempts回', warning: true),
                if (answer.round1Partial)
                  const _MetaChip(label: 'PARTIAL', warning: true),
              ],
            ),
          ),
        _Markdown(text: answer.round1Text),
      ],
    );
  }
}

class _SynthesisCard extends StatelessWidget {
  const _SynthesisCard({
    required this.synthesis,
    required this.pending,
    this.regenerationPending = false,
    this.onRegenerate,
  });

  final SynthesisRecord? synthesis;
  final bool pending;
  final bool regenerationPending;
  final VoidCallback? onRegenerate;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final colors = theme.colorScheme;
    final current = synthesis;
    final failed = current != null && !current.ok && !current.skipped;
    final source = current?.source ?? '';
    final sourceLabel = _providerLabels[source] ?? source;
    return Card(
      margin: EdgeInsets.zero,
      color: colors.secondaryContainer,
      child: Padding(
        padding: const EdgeInsets.all(14),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Icon(
                  failed
                      ? Icons.error_outline
                      : current?.partial == true ||
                            current?.usageMayBeIncomplete == true
                      ? Icons.warning_amber_rounded
                      : Icons.auto_awesome,
                  color:
                      failed ||
                          current?.partial == true ||
                          current?.usageMayBeIncomplete == true
                      ? colors.error
                      : colors.onSecondaryContainer,
                ),
                const SizedBox(width: 8),
                Expanded(
                  child: Text(
                    '統合回答',
                    style: theme.textTheme.titleMedium?.copyWith(
                      color: colors.onSecondaryContainer,
                      fontWeight: FontWeight.bold,
                    ),
                  ),
                ),
                if (pending)
                  const SizedBox.square(
                    dimension: 18,
                    child: CircularProgressIndicator(strokeWidth: 2),
                  )
                else if (onRegenerate != null && current?.skipped != true)
                  IconButton(
                    tooltip: '統合回答を再生成',
                    onPressed: regenerationPending ? null : onRegenerate,
                    icon: regenerationPending
                        ? const SizedBox.square(
                            dimension: 16,
                            child: CircularProgressIndicator(strokeWidth: 2),
                          )
                        : const Icon(Icons.replay_outlined),
                  ),
              ],
            ),
            if (current != null) ...[
              const SizedBox(height: 8),
              Wrap(
                spacing: 6,
                runSpacing: 5,
                children: [
                  if (sourceLabel.isNotEmpty) _MetaChip(label: sourceLabel),
                  if (current.model.trim().isNotEmpty &&
                      !(current.mock &&
                          current.model.trim().toLowerCase() == 'mock'))
                    _MetaChip(label: current.model.trim()),
                  if (current.elapsedSec > 0)
                    _MetaChip(label: _elapsedLabel(current.elapsedSec)),
                  if (current.mock)
                    const _MetaChip(label: 'MOCK', emphasized: true),
                  if (current.skipped)
                    const _MetaChip(label: 'SKIPPED', emphasized: true),
                  if (current.partial)
                    _MetaChip(
                      label: current.incompleteReason.isEmpty
                          ? 'PARTIAL'
                          : 'PARTIAL · ${_reasonLabel(current.incompleteReason)}',
                      warning: true,
                    ),
                  if (_auditAttempts(current.requestAudit) > 1)
                    _MetaChip(
                      label: 'HTTP ${_auditAttempts(current.requestAudit)}回',
                      warning: true,
                    ),
                  if (current.usageMayBeIncomplete)
                    const _MetaChip(label: '利用量は不完全な可能性', warning: true),
                ],
              ),
            ],
            const SizedBox(height: 10),
            if (pending)
              Text(
                '各回答をもとに統合しています…',
                style: TextStyle(color: colors.onSecondaryContainer),
              )
            else if (current == null)
              Text(
                '統合回答はありません。',
                style: TextStyle(color: colors.onSecondaryContainer),
              )
            else if (current.skipped)
              Text(
                'このターンでは統合をスキップしました。',
                style: TextStyle(color: colors.onSecondaryContainer),
              )
            else if (failed) ...[
              _ErrorBox(
                message: current.error.isEmpty
                    ? '統合回答の生成に失敗しました。'
                    : current.error,
              ),
              if (current.text.trim().isNotEmpty) ...[
                const SizedBox(height: 10),
                _Markdown(text: current.text),
              ],
            ] else if (current.partial) ...[
              _ErrorBox(
                title: '部分回答',
                message: _partialMessage(current.incompleteReason),
                warning: true,
              ),
              if (current.text.trim().isNotEmpty) ...[
                const SizedBox(height: 10),
                _Markdown(text: current.text),
              ],
            ] else if (current.text.trim().isEmpty)
              Text(
                '（統合回答は空です）',
                style: TextStyle(color: colors.onSecondaryContainer),
              )
            else
              _Markdown(text: current.text),
          ],
        ),
      ),
    );
  }
}

class _RevisionSummary extends StatelessWidget {
  const _RevisionSummary({required this.attempts});

  final List<RegenerationAttempt> attempts;

  @override
  Widget build(BuildContext context) {
    final generated = attempts.where((attempt) => !attempt.original).toList();
    return Card(
      margin: EdgeInsets.zero,
      child: ExpansionTile(
        leading: const Icon(Icons.account_tree_outlined),
        title: Text('再生成履歴 ${generated.length}件'),
        subtitle: const Text('元の結果を保持したまま、新しいattemptとして記録します'),
        children: [
          for (final attempt in attempts)
            ListTile(
              dense: true,
              leading: Icon(
                attempt.original
                    ? Icons.history
                    : attempt.status == 'completed'
                    ? Icons.check_circle_outline
                    : attempt.status == 'failed'
                    ? Icons.error_outline
                    : Icons.pending_outlined,
              ),
              title: Text(
                attempt.original
                    ? '元の${attempt.target == 'synthesis' ? '統合' : '回答'}'
                    : '${attempt.target == 'synthesis' ? '統合' : attempt.provider} · ${attempt.status}',
              ),
              subtitle: attempt.createdAt.isEmpty
                  ? null
                  : Text(attempt.createdAt),
            ),
        ],
      ),
    );
  }
}

class _ErrorBox extends StatelessWidget {
  const _ErrorBox({
    required this.message,
    this.title = 'エラー',
    this.warning = false,
  });

  final String title;
  final String message;
  final bool warning;

  @override
  Widget build(BuildContext context) {
    final colors = Theme.of(context).colorScheme;
    final background = warning
        ? colors.tertiaryContainer
        : colors.errorContainer;
    final foreground = warning
        ? colors.onTertiaryContainer
        : colors.onErrorContainer;
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.all(10),
      decoration: BoxDecoration(
        color: background,
        borderRadius: BorderRadius.circular(10),
      ),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Icon(
            warning ? Icons.warning_amber_rounded : Icons.error_outline,
            color: foreground,
            size: 20,
          ),
          const SizedBox(width: 8),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  title,
                  style: TextStyle(
                    color: foreground,
                    fontWeight: FontWeight.bold,
                  ),
                ),
                SelectableText(message, style: TextStyle(color: foreground)),
              ],
            ),
          ),
        ],
      ),
    );
  }
}

class _Markdown extends StatelessWidget {
  const _Markdown({required this.text});

  final String text;

  @override
  Widget build(BuildContext context) => SizedBox(
    width: double.infinity,
    child: MarkdownBody(
      data: text,
      selectable: true,
      onTapLink: (label, href, title) {
        if (href != null) _openHttpUrl(href);
      },
    ),
  );
}

List<String> _orderedProviders(Iterable<String> names) {
  final present = names.where((name) => name.isNotEmpty).toSet();
  return [..._providerOrder.where(present.remove), ...present.toList()..sort()];
}

String _elapsedLabel(double seconds) {
  if (seconds < 10) return '${seconds.toStringAsFixed(1)}秒';
  return '${seconds.round()}秒';
}

int _auditAttempts(Map<String, dynamic> audit) {
  final value = audit['http_attempts'];
  return value is int && value >= 0 ? value : 0;
}

String _reasonLabel(String reason) => switch (reason) {
  'max_output_tokens' || 'max_tokens' => '出力上限',
  'content_filter' => '安全フィルター',
  'model_context_window_exceeded' => '文脈上限',
  _ => reason,
};

String _partialMessage(String reason) {
  final suffix = reason.isEmpty ? '' : '（${_reasonLabel(reason)}）';
  return 'Providerが未完了状態で返した本文です$suffix。完全な回答として扱わないでください。';
}

String _savedTurnNotice(TurnRecord turn) {
  if (turn.cancelled) return 'この会議はキャンセルされ、利用量が不完全な可能性があります。';
  if (turn.interrupted || turn.status == 'interrupted') {
    return 'この会議はサーバー停止で中断されました。自動再実行はせず、利用量は不完全な可能性があります。';
  }
  if (turn.status == 'running') {
    return 'このrunは実行中、または接続が切れて状態確認待ちです。再接続して進捗を復元するか停止を要求できます。利用量は不完全な可能性があります。';
  }
  if (turn.failed && turn.usageMayBeIncomplete) {
    return 'この会議はエラーで完了せず、Provider側の処理・利用量も不完全な可能性があります。';
  }
  if (turn.failed) return 'この会議はエラーで完了しませんでした。';
  if (turn.usageMayBeIncomplete) return 'この会議の利用量は不完全な可能性があります。';
  return '';
}
