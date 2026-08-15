import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_markdown_plus/flutter_markdown_plus.dart';
import 'package:url_launcher/url_launcher.dart';

import '../models.dart';
import '../provider_catalog.dart';
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
    this.showTokenUsageLedger = true,
  });

  final TurnRecord turn;
  final VoidCallback? onReconnect;
  final VoidCallback? onCancel;
  final ValueChanged<String>? onRegenerateAnswer;
  final VoidCallback? onRegenerateSynthesis;
  final VoidCallback? onForkEdit;
  final bool actionPending;
  final bool regenerationPending;
  final bool showTokenUsageLedger;

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
      reasoningMode: turn.options['reasoning_mode']?.toString() ?? '',
      regenerationPending: regenerationPending,
      onRegenerateAnswer: turn.status == 'completed'
          ? onRegenerateAnswer
          : null,
      onRegenerateSynthesis: turn.status == 'completed'
          ? onRegenerateSynthesis
          : null,
      onForkEdit: turn.status == 'completed' ? onForkEdit : null,
      attachments: turn.attachments,
      showTokenUsageLedger: showTokenUsageLedger,
      usageLedgerStorageKey: PageStorageKey<String>(
        'usage-ledger-${turn.requestId}',
      ),
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
  const LiveTurnView({
    super.key,
    required this.turn,
    this.showTokenUsageLedger = true,
  });

  final LiveTurn turn;
  final bool showTokenUsageLedger;

  @override
  Widget build(BuildContext context) {
    final names = <String>{...turn.providers, ...turn.answers.keys};
    return _TurnView(
      message: turn.message,
      answers: turn.answers,
      providerNames: names.isEmpty ? providerOrder : names,
      synthesis: turn.synthesis,
      insights: turn.insights,
      livePhase: turn.phase,
      liveError: turn.error,
      synthesisPending: turn.synthesis == null && turn.error.isEmpty,
      reasoningMode: turn.reasoningMode,
      showTokenUsageLedger: showTokenUsageLedger,
      usageLedgerStorageKey: PageStorageKey<String>(
        'usage-ledger-${turn.requestId}',
      ),
    );
  }
}

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
    this.reasoningMode = '',
    this.regenerationPending = false,
    this.onRegenerateAnswer,
    this.onRegenerateSynthesis,
    this.onForkEdit,
    this.attachments = const [],
    this.showTokenUsageLedger = true,
    this.usageLedgerStorageKey,
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
  final String reasoningMode;
  final bool regenerationPending;
  final ValueChanged<String>? onRegenerateAnswer;
  final VoidCallback? onRegenerateSynthesis;
  final VoidCallback? onForkEdit;
  final List<AttachmentRecord> attachments;
  final bool showTokenUsageLedger;
  final Key? usageLedgerStorageKey;

  @override
  Widget build(BuildContext context) {
    final providers = _orderedProviders(providerNames);
    final usageEntries = <Map<String, dynamic>>[
      for (final entry in answers.entries)
        if (entry.value.usage.isNotEmpty)
          {
            'source': providerLabels[entry.key] ?? entry.key,
            'model': entry.value.model,
            'phase': entry.value.round > 1 ? '回答 + DEBATE' : '回答',
            'usage': entry.value.usage,
          },
      if (synthesis != null && synthesis!.usage.isNotEmpty)
        {
          'source':
              '統合 · ${providerLabels[synthesis!.source] ?? synthesis!.source}',
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
            reasoningMode: reasoningMode,
            attempts: attempts
                .where(
                  (attempt) =>
                      attempt.target == 'answer' &&
                      attempt.provider == provider,
                )
                .toList(growable: false),
            pending: answers[provider] == null && liveError.isEmpty,
            regenerationPending: regenerationPending,
            onRegenerate: onRegenerateAnswer == null
                ? null
                : () => onRegenerateAnswer!(provider),
          ),
          const SizedBox(height: 8),
        ],
        if (insights?.isNotEmpty == true ||
            (showTokenUsageLedger && usageEntries.isNotEmpty)) ...[
          InsightsPanel(
            insights: insights,
            usageEntries: usageEntries,
            showUsageLedger: showTokenUsageLedger,
            usageLedgerStorageKey: usageLedgerStorageKey,
          ),
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
    final completed = phase == '完了が先に確定しました';
    final stopped = phase == 'ローカル停止処理が完了しました' || phase == 'すでに停止しています';
    final terminalFailure = phase == '停止前に処理失敗が確定しました';
    final failed = error.isNotEmpty || terminalFailure;
    final settled = completed || stopped || terminalFailure;
    final message = error.isNotEmpty ? error : phase;
    final colors = theme.colorScheme;
    return Semantics(
      liveRegion: true,
      label: message,
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
            else if (settled)
              Icon(completed ? Icons.check_circle_outline : Icons.stop_circle)
            else
              const SizedBox.square(
                dimension: 18,
                child: CircularProgressIndicator(strokeWidth: 2),
              ),
            const SizedBox(width: 10),
            Expanded(
              child: SelectableText(
                message,
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
    required this.reasoningMode,
    required this.attempts,
    required this.pending,
    this.regenerationPending = false,
    this.onRegenerate,
  });

  final String provider;
  final AnswerRecord? answer;
  final String reasoningMode;
  final List<RegenerationAttempt> attempts;
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
        tilePadding: const EdgeInsets.symmetric(horizontal: 10),
        childrenPadding: const EdgeInsets.fromLTRB(12, 0, 12, 12),
        leading: Container(
          width: 3,
          height: 26,
          decoration: BoxDecoration(
            color: accent,
            borderRadius: BorderRadius.circular(2),
          ),
        ),
        title: _AnswerHeader(
          provider: provider,
          answer: current,
          reasoningMode: reasoningMode,
          pending: pending,
          regenerationPending: regenerationPending,
          onRegenerate: onRegenerate,
          accent: accent,
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
            _Metadata(
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
            const SizedBox(height: 10),
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
            if (hasInitial) ...[
              const SizedBox(height: 8),
              _InitialAnswer(answer: current),
            ],
            if (attempts.isNotEmpty) ...[
              const SizedBox(height: 8),
              _ProviderAttemptHistory(provider: provider, attempts: attempts),
            ],
            if (current.text.trim().isNotEmpty) ...[
              if (!current.ok || hasInitial || attempts.isNotEmpty)
                const SizedBox(height: 10),
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
          ],
        ],
      ),
    );
  }
}

class _AnswerHeader extends StatelessWidget {
  const _AnswerHeader({
    required this.provider,
    required this.answer,
    required this.reasoningMode,
    required this.pending,
    required this.regenerationPending,
    required this.onRegenerate,
    required this.accent,
  });

  final String provider;
  final AnswerRecord? answer;
  final String reasoningMode;
  final bool pending;
  final bool regenerationPending;
  final VoidCallback? onRegenerate;
  final Color accent;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final label = providerLabels[provider] ?? provider;
    final status = _answerStatus(answer, pending: pending);
    final metadata = _compactAnswerMetadata(
      answer,
      reasoningMode: reasoningMode,
      pending: pending,
    );
    return Semantics(
      container: true,
      label: '$label、${status.label}${metadata.isEmpty ? '' : '、$metadata'}',
      child: Row(
        children: [
          ConstrainedBox(
            constraints: const BoxConstraints(maxWidth: 74),
            child: Text(
              label,
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
              style: theme.textTheme.titleSmall?.copyWith(
                color: accent,
                fontWeight: FontWeight.bold,
              ),
            ),
          ),
          const SizedBox(width: 7),
          Expanded(
            child: Tooltip(
              message: metadata.isEmpty ? status.label : metadata,
              child: Text(
                metadata,
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
                style: theme.textTheme.labelSmall?.copyWith(
                  color: theme.colorScheme.onSurfaceVariant,
                ),
              ),
            ),
          ),
          _AnswerStatusIcon(status: status, accent: accent),
          if (answer?.text.trim().isNotEmpty == true)
            _CompactIconButton(
              tooltip: '$labelの回答をコピー',
              icon: Icons.content_copy_outlined,
              onPressed: () => _copyText(context, answer!.text, '$labelの回答'),
            ),
          if (onRegenerate != null && answer != null)
            _CompactIconButton(
              tooltip: '$labelの回答を再生成',
              icon: Icons.replay_outlined,
              onPressed: regenerationPending ? null : onRegenerate,
              progress: regenerationPending,
            ),
        ],
      ),
    );
  }
}

class _CompactIconButton extends StatelessWidget {
  const _CompactIconButton({
    required this.tooltip,
    required this.icon,
    required this.onPressed,
    this.progress = false,
  });

  final String tooltip;
  final IconData icon;
  final VoidCallback? onPressed;
  final bool progress;

  @override
  Widget build(BuildContext context) => IconButton(
    tooltip: tooltip,
    visualDensity: VisualDensity.compact,
    constraints: const BoxConstraints.tightFor(width: 32, height: 32),
    padding: const EdgeInsets.all(5),
    onPressed: onPressed,
    icon: progress
        ? const SizedBox.square(
            dimension: 15,
            child: CircularProgressIndicator(strokeWidth: 2),
          )
        : Icon(icon, size: 19),
  );
}

class _AnswerStatus {
  const _AnswerStatus(
    this.label,
    this.icon, {
    this.warning = false,
    this.progress = false,
  });

  final String label;
  final IconData icon;
  final bool warning;
  final bool progress;
}

class _AnswerStatusIcon extends StatelessWidget {
  const _AnswerStatusIcon({required this.status, required this.accent});

  final _AnswerStatus status;
  final Color accent;

  @override
  Widget build(BuildContext context) => Tooltip(
    message: status.label,
    child: SizedBox.square(
      dimension: 26,
      child: status.progress
          ? Padding(
              padding: const EdgeInsets.all(4),
              child: CircularProgressIndicator(strokeWidth: 2, color: accent),
            )
          : Icon(
              status.icon,
              size: 18,
              color: status.warning
                  ? Theme.of(context).colorScheme.error
                  : accent,
            ),
    ),
  );
}

_AnswerStatus _answerStatus(AnswerRecord? answer, {required bool pending}) {
  if (pending) {
    return const _AnswerStatus('生成中', Icons.pending_outlined, progress: true);
  }
  if (answer == null) {
    return const _AnswerStatus('回答なし', Icons.remove_circle_outline);
  }
  if (answer.partial) {
    return const _AnswerStatus(
      '部分回答',
      Icons.warning_amber_rounded,
      warning: true,
    );
  }
  if (!answer.ok) {
    return const _AnswerStatus('失敗', Icons.error_outline, warning: true);
  }
  if (answer.usageMayBeIncomplete) {
    return const _AnswerStatus(
      '利用量を要確認',
      Icons.warning_amber_rounded,
      warning: true,
    );
  }
  return const _AnswerStatus('完了', Icons.check_circle_outline);
}

String _compactAnswerMetadata(
  AnswerRecord? answer, {
  required String reasoningMode,
  required bool pending,
}) {
  if (answer == null) return pending ? '回答待ち…' : '回答なし';
  final model = answer.model.trim();
  final effectiveEffort =
      answer.reasoning['effective']?.toString().trim() ?? '';
  final effort = effectiveEffort.isNotEmpty
      ? effectiveEffort
      : reasoningMode.trim();
  return [
    if (model.isNotEmpty && !(answer.mock && model.toLowerCase() == 'mock'))
      model,
    if (effort.isNotEmpty)
      effort == 'provider_default'
          ? 'effort PROVIDER'
          : 'effort ${effort.toUpperCase()}',
    if (answer.elapsedSec > 0) _elapsedLabel(answer.elapsedSec),
    if (answer.round > 1) 'DEBATE R${answer.round}',
    if (answer.round1Text.trim().isNotEmpty) '批評前あり',
    if (answer.mock) 'MOCK',
    if (answer.webSearchRequested) 'WEB',
  ].join(' · ');
}

void _copyText(BuildContext context, String text, String label) {
  unawaited(Clipboard.setData(ClipboardData(text: text)));
  final messenger = ScaffoldMessenger.maybeOf(context);
  messenger
    ?..hideCurrentSnackBar()
    ..showSnackBar(
      SnackBar(
        content: Text('$labelをコピーしました'),
        duration: const Duration(seconds: 2),
      ),
    );
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
    final colors = Theme.of(context).colorScheme;
    return Material(
      color: colors.surfaceContainerLow,
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(10),
        side: BorderSide(color: colors.outlineVariant),
      ),
      clipBehavior: Clip.antiAlias,
      child: ExpansionTile(
        maintainState: true,
        tilePadding: const EdgeInsets.only(left: 10, right: 6),
        childrenPadding: const EdgeInsets.fromLTRB(10, 0, 10, 10),
        title: Row(
          children: [
            const Expanded(
              child: Text(
                '最初の回答（批評前）',
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
                style: TextStyle(fontWeight: FontWeight.bold),
              ),
            ),
            _CompactIconButton(
              tooltip: '最初の回答をコピー',
              icon: Icons.content_copy_outlined,
              onPressed: () => _copyText(context, answer.round1Text, '最初の回答'),
            ),
          ],
        ),
        leading: const Icon(Icons.history, size: 20),
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
          if (answer.round1Partial) ...[
            const _ErrorBox(
              title: '最初の回答は部分回答です',
              message: '批評前の本文が途中で終了しています。完全な回答として扱わないでください。',
              warning: true,
            ),
            const SizedBox(height: 8),
          ],
          _Markdown(text: answer.round1Text),
        ],
      ),
    );
  }
}

class _ProviderAttemptHistory extends StatelessWidget {
  const _ProviderAttemptHistory({
    required this.provider,
    required this.attempts,
  });

  final String provider;
  final List<RegenerationAttempt> attempts;

  @override
  Widget build(BuildContext context) {
    final colors = Theme.of(context).colorScheme;
    final regenerated = attempts.where((attempt) => !attempt.original).length;
    return Material(
      color: Colors.transparent,
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(10),
        side: BorderSide(color: colors.outlineVariant),
      ),
      clipBehavior: Clip.antiAlias,
      child: ExpansionTile(
        maintainState: true,
        tilePadding: const EdgeInsets.symmetric(horizontal: 10),
        childrenPadding: const EdgeInsets.fromLTRB(10, 0, 10, 8),
        leading: const Icon(Icons.account_tree_outlined, size: 20),
        title: Text(
          '保存された回答履歴 ${attempts.length}件',
          maxLines: 1,
          overflow: TextOverflow.ellipsis,
          style: const TextStyle(fontWeight: FontWeight.bold),
        ),
        children: [
          Align(
            alignment: Alignment.centerLeft,
            child: Text(
              regenerated == 0
                  ? '最初の回答をimmutable attemptとして保持しています。'
                  : '最初の回答を保持したまま、再生成を$regenerated件記録しています。',
              style: Theme.of(
                context,
              ).textTheme.bodySmall?.copyWith(color: colors.onSurfaceVariant),
            ),
          ),
          const SizedBox(height: 4),
          for (final attempt in attempts)
            _AttemptHistoryTile(provider: provider, attempt: attempt),
        ],
      ),
    );
  }
}

class _AttemptHistoryTile extends StatelessWidget {
  const _AttemptHistoryTile({required this.provider, required this.attempt});

  final String provider;
  final RegenerationAttempt attempt;

  @override
  Widget build(BuildContext context) {
    final colors = Theme.of(context).colorScheme;
    final result = attempt.result.isEmpty
        ? null
        : AnswerRecord.fromJson(attempt.result);
    final text = result?.text.trim() ?? '';
    final title = attempt.original
        ? '最初の保存回答'
        : '${providerLabels[provider] ?? provider} · ${_attemptStatusLabel(attempt.status)}';
    final leading = Icon(
      attempt.usageMayBeIncomplete
          ? Icons.warning_amber_rounded
          : attempt.original
          ? Icons.history
          : attempt.status == 'completed'
          ? Icons.check_circle_outline
          : attempt.status == 'failed'
          ? Icons.error_outline
          : Icons.pending_outlined,
      color: attempt.usageMayBeIncomplete ? colors.error : null,
    );
    final subtitleParts = [
      if (result?.model.trim().isNotEmpty == true) result!.model.trim(),
      if (attempt.createdAt.isNotEmpty) attempt.createdAt,
    ];
    if (text.isEmpty) {
      return ListTile(
        dense: true,
        contentPadding: EdgeInsets.zero,
        visualDensity: VisualDensity.compact,
        leading: leading,
        title: Text(title, maxLines: 1, overflow: TextOverflow.ellipsis),
        subtitle: subtitleParts.isEmpty
            ? null
            : Text(
                subtitleParts.join(' · '),
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
              ),
      );
    }
    return ExpansionTile(
      key: ValueKey('attempt-${attempt.attemptId}'),
      maintainState: true,
      tilePadding: EdgeInsets.zero,
      childrenPadding: const EdgeInsets.only(left: 8, right: 8, bottom: 10),
      visualDensity: VisualDensity.compact,
      leading: leading,
      title: Row(
        children: [
          Expanded(
            child: Text(title, maxLines: 1, overflow: TextOverflow.ellipsis),
          ),
          _CompactIconButton(
            tooltip: '$titleをコピー',
            icon: Icons.content_copy_outlined,
            onPressed: () => _copyText(context, text, title),
          ),
        ],
      ),
      subtitle: subtitleParts.isEmpty
          ? null
          : Text(
              subtitleParts.join(' · '),
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
            ),
      children: [
        if (attempt.usageMayBeIncomplete || result?.partial == true) ...[
          const _ErrorBox(
            title: '利用量・本文を要確認',
            message: 'この試行は中断または部分完了の可能性があります。完全な回答として扱わないでください。',
            warning: true,
          ),
          const SizedBox(height: 8),
        ],
        _Markdown(text: text),
      ],
    );
  }
}

String _attemptStatusLabel(String status) => switch (status) {
  'completed' => '完了',
  'failed' => '失敗',
  'running' || 'pending' => '実行中',
  'partial' => '部分回答',
  _ => status.isEmpty ? '状態不明' : status,
};

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
    final sourceLabel = providerLabels[source] ?? source;
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
  return [...providerOrder.where(present.remove), ...present.toList()..sort()];
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
