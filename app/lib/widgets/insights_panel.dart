import 'dart:math' as math;

import 'package:flutter/material.dart';

/// 回答間の語彙比較とProvider実測トークンを表示する自己完結パネル。
///
/// [insights] はバックエンドの `deterministic_lexical_overlap` 系レスポンスを、
/// [usageEntries] は `{source, model, phase, usage: {...}}` 形式の項目を受け取る。
/// 未知のキーや不正な値は無視し、欠損したトークン値を推計しない。
class InsightsPanel extends StatelessWidget {
  const InsightsPanel({
    super.key,
    this.insights,
    this.usageEntries = const [],
    this.showUsageLedger = true,
    this.usageLedgerStorageKey,
  });

  final Map<String, dynamic>? insights;
  final List<Map<String, dynamic>> usageEntries;
  final bool showUsageLedger;
  final Key? usageLedgerStorageKey;

  @override
  Widget build(BuildContext context) {
    final hasInsights = insights?.isNotEmpty == true;
    final hasUsage = showUsageLedger && usageEntries.isNotEmpty;
    if (!hasInsights && !hasUsage) return const SizedBox.shrink();

    final insightData = hasInsights ? _InsightData.from(insights!) : null;
    final ledger = hasUsage
        ? usageEntries.indexed
              .map((entry) => _UsageData.from(entry.$2, entry.$1))
              .toList(growable: false)
        : const <_UsageData>[];
    final semanticsLabel = hasInsights && hasUsage
        ? '回答比較インサイトとトークン利用量台帳'
        : hasInsights
        ? '回答比較インサイト'
        : 'トークン利用量台帳';

    return Semantics(
      container: true,
      label: semanticsLabel,
      child: LayoutBuilder(
        builder: (context, constraints) {
          final children = <Widget>[
            if (insightData != null) _InsightsSection(data: insightData),
            if (ledger.isNotEmpty)
              _UsageSection(entries: ledger, storageKey: usageLedgerStorageKey),
          ];
          if (constraints.maxWidth >= 840 && children.length == 2) {
            return Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Expanded(child: children.first),
                const SizedBox(width: 12),
                Expanded(child: children.last),
              ],
            );
          }
          return Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              for (var index = 0; index < children.length; index++) ...[
                if (index > 0) const SizedBox(height: 12),
                children[index],
              ],
            ],
          );
        },
      ),
    );
  }
}

class _InsightsSection extends StatelessWidget {
  const _InsightsSection({required this.data});

  final _InsightData data;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return _PanelCard(
      semanticLabel: '回答間の語彙比較',
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          const _SectionHeader(icon: Icons.join_inner, title: '回答間の語彙比較'),
          const SizedBox(height: 6),
          Text(
            '文字と語彙の重なりです。回答の正しさ・品質・AIの確信度を評価するものではありません。',
            style: theme.textTheme.bodySmall?.copyWith(
              color: theme.colorScheme.onSurfaceVariant,
            ),
          ),
          const SizedBox(height: 14),
          _AgreementScore(data: data),
          if (data.providerScores.isNotEmpty) ...[
            const SizedBox(height: 16),
            _Subheading('回答別の平均類似度'),
            const SizedBox(height: 8),
            _ScoreList(scores: data.providerScores),
          ],
          if (data.pairs.isNotEmpty) ...[
            const SizedBox(height: 16),
            _Subheading('回答ペア別'),
            const SizedBox(height: 8),
            for (final pair in data.pairs)
              _ScoreLine(label: pair.label, score: pair.score),
          ],
          const SizedBox(height: 16),
          _TermsBlock(title: '共有語', terms: data.sharedTerms),
          if (data.distinctiveTerms.isNotEmpty) ...[
            const SizedBox(height: 14),
            _Subheading('各Providerだけに現れた語'),
            const SizedBox(height: 8),
            for (final entry in data.distinctiveTerms.entries) ...[
              Text(entry.key, style: theme.textTheme.labelLarge),
              const SizedBox(height: 5),
              _TermWrap(terms: entry.value),
              const SizedBox(height: 9),
            ],
          ],
          if (data.cautions.isNotEmpty) ...[
            const SizedBox(height: 8),
            _Subheading('表現上の確認ポイント'),
            const SizedBox(height: 8),
            for (final caution in data.cautions)
              Padding(
                padding: const EdgeInsets.only(bottom: 8),
                child: _CautionTile(caution: caution),
              ),
          ],
          if (data.ignoredSources.isNotEmpty) ...[
            const SizedBox(height: 8),
            Text(
              '空回答のため比較対象外: ${data.ignoredSources.join(', ')}',
              style: theme.textTheme.bodySmall?.copyWith(
                color: theme.colorScheme.onSurfaceVariant,
              ),
            ),
          ],
          const SizedBox(height: 8),
          _Limitations(text: data.limitations),
        ],
      ),
    );
  }
}

class _AgreementScore extends StatelessWidget {
  const _AgreementScore({required this.data});

  final _InsightData data;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final comparable = data.comparable && data.agreementScore != null;
    final valueLabel = comparable ? _percent(data.agreementScore) : '比較不能';
    final semanticsLabel = comparable
        ? '回答間の語彙的一致 $valueLabel。正しさや確信度の評価ではありません'
        : '回答間の語彙的一致は比較できる回答が2件未満のため算出不能';
    return Semantics(
      container: true,
      label: semanticsLabel,
      child: ExcludeSemantics(
        child: DecoratedBox(
          decoration: BoxDecoration(
            color: theme.colorScheme.surfaceContainerHigh,
            borderRadius: BorderRadius.circular(12),
          ),
          child: Padding(
            padding: const EdgeInsets.all(12),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                Row(
                  children: [
                    Expanded(
                      child: Text('語彙的一致', style: theme.textTheme.titleSmall),
                    ),
                    Text(
                      valueLabel,
                      style: theme.textTheme.titleLarge?.copyWith(
                        fontFeatures: const [FontFeature.tabularFigures()],
                      ),
                    ),
                  ],
                ),
                const SizedBox(height: 7),
                if (comparable)
                  ClipRRect(
                    borderRadius: BorderRadius.circular(99),
                    child: LinearProgressIndicator(
                      value: data.agreementScore,
                      minHeight: 6,
                      color: theme.colorScheme.onSurfaceVariant,
                      backgroundColor:
                          theme.colorScheme.surfaceContainerHighest,
                    ),
                  )
                else
                  Text('比較できる回答が2件以上必要です。', style: theme.textTheme.bodySmall),
                const SizedBox(height: 6),
                Text(
                  '${data.answerCount}回答・${data.comparisonCount}ペアを対象',
                  style: theme.textTheme.bodySmall?.copyWith(
                    color: theme.colorScheme.onSurfaceVariant,
                  ),
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }
}

class _ScoreList extends StatelessWidget {
  const _ScoreList({required this.scores});

  final Map<String, double?> scores;

  @override
  Widget build(BuildContext context) => Column(
    children: [
      for (final entry in scores.entries)
        _ScoreLine(label: entry.key, score: entry.value),
    ],
  );
}

class _ScoreLine extends StatelessWidget {
  const _ScoreLine({required this.label, required this.score});

  final String label;
  final double? score;

  @override
  Widget build(BuildContext context) => Padding(
    padding: const EdgeInsets.symmetric(vertical: 3),
    child: Row(
      children: [
        Expanded(
          child: Text(label, overflow: TextOverflow.ellipsis, maxLines: 2),
        ),
        const SizedBox(width: 8),
        Text(
          _percent(score),
          style: const TextStyle(fontFeatures: [FontFeature.tabularFigures()]),
        ),
      ],
    ),
  );
}

class _TermsBlock extends StatelessWidget {
  const _TermsBlock({required this.title, required this.terms});

  final String title;
  final List<String> terms;

  @override
  Widget build(BuildContext context) => Column(
    crossAxisAlignment: CrossAxisAlignment.start,
    children: [
      _Subheading(title),
      const SizedBox(height: 8),
      _TermWrap(terms: terms),
    ],
  );
}

class _TermWrap extends StatelessWidget {
  const _TermWrap({required this.terms});

  final List<String> terms;

  @override
  Widget build(BuildContext context) {
    if (terms.isEmpty) {
      return Text(
        '該当なし',
        style: Theme.of(context).textTheme.bodySmall?.copyWith(
          color: Theme.of(context).colorScheme.onSurfaceVariant,
        ),
      );
    }
    return Wrap(
      spacing: 6,
      runSpacing: 6,
      children: [for (final term in terms) _TermChip(term: term)],
    );
  }
}

class _TermChip extends StatelessWidget {
  const _TermChip({required this.term});

  final String term;

  @override
  Widget build(BuildContext context) {
    final colors = Theme.of(context).colorScheme;
    return Container(
      constraints: const BoxConstraints(maxWidth: 260),
      padding: const EdgeInsets.symmetric(horizontal: 9, vertical: 5),
      decoration: BoxDecoration(
        color: colors.surfaceContainerHighest,
        borderRadius: BorderRadius.circular(99),
      ),
      child: Text(term, overflow: TextOverflow.ellipsis, maxLines: 1),
    );
  }
}

class _CautionTile extends StatelessWidget {
  const _CautionTile({required this.caution});

  final _CautionData caution;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final title = [
      caution.source,
      caution.label,
      if (caution.count != null) '${caution.count}件',
    ].where((value) => value.isNotEmpty).join(' · ');
    return Semantics(
      container: true,
      label: '表現上の確認ポイント $title',
      child: DecoratedBox(
        decoration: BoxDecoration(
          color: theme.colorScheme.surfaceContainerLow,
          borderRadius: BorderRadius.circular(10),
          border: Border.all(color: theme.colorScheme.outlineVariant),
        ),
        child: Padding(
          padding: const EdgeInsets.all(10),
          child: Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              ExcludeSemantics(
                child: Icon(
                  Icons.manage_search,
                  size: 20,
                  color: theme.colorScheme.onSurfaceVariant,
                ),
              ),
              const SizedBox(width: 8),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(title, style: theme.textTheme.labelLarge),
                    if (caution.matches.isNotEmpty) ...[
                      const SizedBox(height: 4),
                      Text('検出語: ${caution.matches.join(', ')}'),
                    ],
                    if (caution.description.isNotEmpty) ...[
                      const SizedBox(height: 4),
                      Text(
                        caution.description,
                        style: theme.textTheme.bodySmall?.copyWith(
                          color: theme.colorScheme.onSurfaceVariant,
                        ),
                      ),
                    ],
                  ],
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

class _Limitations extends StatelessWidget {
  const _Limitations({required this.text});

  final String text;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Semantics(
      container: true,
      label: '解析の限界。$text',
      child: DecoratedBox(
        decoration: BoxDecoration(
          color: theme.colorScheme.surfaceContainerLow,
          borderRadius: BorderRadius.circular(10),
        ),
        child: Padding(
          padding: const EdgeInsets.all(10),
          child: Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              ExcludeSemantics(
                child: Icon(
                  Icons.info_outline,
                  size: 19,
                  color: theme.colorScheme.onSurfaceVariant,
                ),
              ),
              const SizedBox(width: 8),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text('この解析の限界', style: theme.textTheme.labelLarge),
                    const SizedBox(height: 3),
                    Text(text, style: theme.textTheme.bodySmall),
                  ],
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

class _UsageSection extends StatelessWidget {
  const _UsageSection({required this.entries, this.storageKey});

  final List<_UsageData> entries;
  final Key? storageKey;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Semantics(
      container: true,
      label: 'Provider実測トークン利用量台帳',
      child: Card(
        margin: EdgeInsets.zero,
        clipBehavior: Clip.antiAlias,
        child: ExpansionTile(
          key: storageKey,
          initiallyExpanded: false,
          maintainState: false,
          leading: const ExcludeSemantics(
            child: Icon(Icons.receipt_long_outlined),
          ),
          title: Semantics(
            header: true,
            child: Text('トークン利用量台帳', style: theme.textTheme.titleMedium),
          ),
          subtitle: Text(
            '実測値 ${entries.length}件・推計や課金額ではありません',
            style: theme.textTheme.bodySmall?.copyWith(
              color: theme.colorScheme.onSurfaceVariant,
            ),
          ),
          childrenPadding: const EdgeInsets.fromLTRB(14, 0, 14, 14),
          children: [
            Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                Text(
                  'Provider応答に含まれる実測値だけを表示します。欠損値は「—」で、'
                  'OFFにしてもusageの取得・端末保存・エクスポートは続きます。',
                  style: theme.textTheme.bodySmall?.copyWith(
                    color: theme.colorScheme.onSurfaceVariant,
                  ),
                ),
                const SizedBox(height: 12),
                for (var index = 0; index < entries.length; index++) ...[
                  if (index > 0) const SizedBox(height: 8),
                  _UsageEntry(entry: entries[index]),
                ],
              ],
            ),
          ],
        ),
      ),
    );
  }
}

class _UsageEntry extends StatelessWidget {
  const _UsageEntry({required this.entry});

  final _UsageData entry;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final details = [
      if (entry.model.isNotEmpty) entry.model,
      if (entry.phase.isNotEmpty) entry.phase,
    ].join(' · ');
    return Semantics(
      container: true,
      label: '${entry.source}のProvider実測トークン',
      child: DecoratedBox(
        decoration: BoxDecoration(
          border: Border.all(color: theme.colorScheme.outlineVariant),
          borderRadius: BorderRadius.circular(11),
        ),
        child: Padding(
          padding: const EdgeInsets.all(11),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              Text(entry.source, style: theme.textTheme.titleSmall),
              if (details.isNotEmpty) ...[
                const SizedBox(height: 2),
                Text(
                  details,
                  maxLines: 2,
                  overflow: TextOverflow.ellipsis,
                  style: theme.textTheme.bodySmall?.copyWith(
                    color: theme.colorScheme.onSurfaceVariant,
                  ),
                ),
              ],
              const SizedBox(height: 9),
              LayoutBuilder(
                builder: (context, constraints) {
                  final itemWidth = constraints.maxWidth >= 420
                      ? (constraints.maxWidth - 16) / 3
                      : (constraints.maxWidth - 8) / 2;
                  return Wrap(
                    spacing: 8,
                    runSpacing: 8,
                    children: [
                      for (final metric in entry.metrics)
                        SizedBox(
                          width: math.max(112, itemWidth),
                          child: _TokenMetric(metric: metric),
                        ),
                    ],
                  );
                },
              ),
              if (!entry.hasAnyValue) ...[
                const SizedBox(height: 8),
                Text(
                  'このProviderからトークン値は返されていません。',
                  style: theme.textTheme.bodySmall?.copyWith(
                    color: theme.colorScheme.onSurfaceVariant,
                  ),
                ),
              ],
            ],
          ),
        ),
      ),
    );
  }
}

class _TokenMetric extends StatelessWidget {
  const _TokenMetric({required this.metric});

  final _UsageMetric metric;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return DecoratedBox(
      decoration: BoxDecoration(
        color: theme.colorScheme.surfaceContainerHigh,
        borderRadius: BorderRadius.circular(9),
      ),
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: 9, vertical: 7),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              metric.label,
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
              style: theme.textTheme.labelSmall?.copyWith(
                color: theme.colorScheme.onSurfaceVariant,
              ),
            ),
            const SizedBox(height: 2),
            Text(
              _integer(metric.value),
              maxLines: 1,
              style: theme.textTheme.titleSmall?.copyWith(
                fontFeatures: const [FontFeature.tabularFigures()],
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class _PanelCard extends StatelessWidget {
  const _PanelCard({required this.semanticLabel, required this.child});

  final String semanticLabel;
  final Widget child;

  @override
  Widget build(BuildContext context) => Semantics(
    container: true,
    label: semanticLabel,
    child: Card(
      margin: EdgeInsets.zero,
      clipBehavior: Clip.antiAlias,
      child: Padding(padding: const EdgeInsets.all(14), child: child),
    ),
  );
}

class _SectionHeader extends StatelessWidget {
  const _SectionHeader({required this.icon, required this.title});

  final IconData icon;
  final String title;

  @override
  Widget build(BuildContext context) => Row(
    children: [
      ExcludeSemantics(child: Icon(icon, size: 22)),
      const SizedBox(width: 8),
      Expanded(
        child: Semantics(
          header: true,
          child: Text(title, style: Theme.of(context).textTheme.titleMedium),
        ),
      ),
    ],
  );
}

class _Subheading extends StatelessWidget {
  const _Subheading(this.text);

  final String text;

  @override
  Widget build(BuildContext context) => Semantics(
    header: true,
    child: Text(text, style: Theme.of(context).textTheme.titleSmall),
  );
}

class _InsightData {
  const _InsightData({
    required this.comparable,
    required this.answerCount,
    required this.comparisonCount,
    required this.agreementScore,
    required this.providerScores,
    required this.pairs,
    required this.sharedTerms,
    required this.distinctiveTerms,
    required this.cautions,
    required this.ignoredSources,
    required this.limitations,
  });

  factory _InsightData.from(Map<String, dynamic> raw) {
    final distinctive = <String, List<String>>{};
    for (final entry in _map(raw['distinctive_terms']).entries) {
      distinctive[entry.key] = _strings(entry.value);
    }

    final providerScores = <String, double?>{};
    for (final entry in _map(raw['provider_similarities']).entries) {
      providerScores[entry.key] = _score(entry.value);
    }

    final pairs = <_PairData>[];
    for (final item in _list(raw['pairwise_similarities'])) {
      final pair = _map(item);
      final sources = _strings(pair['sources']);
      pairs.add(
        _PairData(
          label: sources.isEmpty ? '不明なペア' : sources.join(' ↔ '),
          score: _score(pair['similarity']),
        ),
      );
    }

    final cautions = <_CautionData>[];
    for (final sourceEntry in _map(raw['caution_signals']).entries) {
      for (final item in _list(sourceEntry.value)) {
        final caution = _map(item);
        cautions.add(
          _CautionData(
            source: sourceEntry.key,
            type: _text(caution['type']),
            count: _token(caution['count']),
            matches: _strings(caution['matches']),
            description: _text(caution['description']),
          ),
        );
      }
    }

    final method = _map(raw['method']);
    final limitationText = _text(method['limitations']);
    return _InsightData(
      comparable: raw['is_comparable'] == true,
      answerCount: _token(raw['answer_count']) ?? 0,
      comparisonCount: _token(raw['comparison_count']) ?? pairs.length,
      agreementScore: _score(raw['agreement_score']),
      providerScores: _sortedMap(providerScores),
      pairs: pairs,
      sharedTerms: _strings(raw['shared_terms']),
      distinctiveTerms: _sortedMap(distinctive),
      cautions: cautions,
      ignoredSources: _strings(raw['ignored_sources']),
      limitations: limitationText.isEmpty
          ? '語彙の重なりだけを扱い、意味的一致、事実の正しさ、品質、信頼度、AIの確信度は判定しません。'
          : limitationText,
    );
  }

  final bool comparable;
  final int answerCount;
  final int comparisonCount;
  final double? agreementScore;
  final Map<String, double?> providerScores;
  final List<_PairData> pairs;
  final List<String> sharedTerms;
  final Map<String, List<String>> distinctiveTerms;
  final List<_CautionData> cautions;
  final List<String> ignoredSources;
  final String limitations;
}

class _PairData {
  const _PairData({required this.label, required this.score});

  final String label;
  final double? score;
}

class _CautionData {
  const _CautionData({
    required this.source,
    required this.type,
    required this.count,
    required this.matches,
    required this.description,
  });

  final String source;
  final String type;
  final int? count;
  final List<String> matches;
  final String description;

  String get label => switch (type) {
    'absolute_language' => '強い断定表現',
    'uncertainty_language' => '不確実性表現',
    'numeric_expression' => '数値表現',
    '' => '分類不明',
    _ => type,
  };
}

class _UsageData {
  const _UsageData({
    required this.source,
    required this.model,
    required this.phase,
    required this.metrics,
  });

  factory _UsageData.from(Map<String, dynamic> raw, int index) {
    final nestedUsage = _map(raw['usage']);
    final usage = nestedUsage.isEmpty ? _map(raw) : nestedUsage;
    final source = _firstText(raw, const ['source', 'provider']);
    return _UsageData(
      source: source.isEmpty ? '不明なProvider ${index + 1}' : source,
      model: _firstText(raw, const ['model']),
      phase: _firstText(raw, const ['phase', 'role', 'round']),
      metrics: [
        _UsageMetric(
          label: 'Input',
          value: _readToken(usage, const [
            ['input_tokens'],
            ['prompt_tokens'],
            ['total_input_tokens'],
            ['inputTokenCount'],
            ['promptTokenCount'],
          ]),
        ),
        _UsageMetric(
          label: 'Output',
          value: _readToken(usage, const [
            ['output_tokens'],
            ['completion_tokens'],
            ['total_output_tokens'],
            ['outputTokenCount'],
            ['candidatesTokenCount'],
          ]),
        ),
        _UsageMetric(
          label: 'Total',
          value: _readToken(usage, const [
            ['total_tokens'],
            ['totalTokenCount'],
          ]),
        ),
        _UsageMetric(
          label: 'Cached',
          value: _readToken(usage, const [
            ['cached_tokens'],
            ['cached_input_tokens'],
            ['cache_read_input_tokens'],
            ['cachedContentTokenCount'],
            ['input_tokens_details', 'cached_tokens'],
            ['prompt_tokens_details', 'cached_tokens'],
          ]),
        ),
        _UsageMetric(
          label: 'Reasoning',
          value: _readToken(usage, const [
            ['reasoning_tokens'],
            ['thoughts_token_count'],
            ['thoughtsTokenCount'],
            ['output_tokens_details', 'reasoning_tokens'],
            ['completion_tokens_details', 'reasoning_tokens'],
          ]),
        ),
        _UsageMetric(
          label: 'Tool',
          value: _readToken(usage, const [
            ['tool_tokens'],
            ['tool_use_tokens'],
            ['toolUsePromptTokenCount'],
          ]),
        ),
      ],
    );
  }

  final String source;
  final String model;
  final String phase;
  final List<_UsageMetric> metrics;

  bool get hasAnyValue => metrics.any((metric) => metric.value != null);
}

class _UsageMetric {
  const _UsageMetric({required this.label, required this.value});

  final String label;
  final int? value;
}

Map<String, dynamic> _map(Object? value) {
  if (value is! Map) return const {};
  return value.map((key, item) => MapEntry(key.toString(), item));
}

List<Object?> _list(Object? value) {
  if (value is Iterable && value is! String) return value.toList();
  return const [];
}

List<String> _strings(Object? value) => _list(value)
    .whereType<Object>()
    .map((item) => item.toString().trim())
    .where((item) => item.isNotEmpty)
    .toList(growable: false);

String _text(Object? value) => value is String ? value.trim() : '';

String _firstText(Map<String, dynamic> map, List<String> keys) {
  for (final key in keys) {
    final value = map[key];
    if (value is String && value.trim().isNotEmpty) return value.trim();
    if (value is num && value.isFinite) return value.toString();
  }
  return '';
}

double? _score(Object? value) {
  if (value is! num || !value.isFinite) return null;
  final score = value.toDouble();
  return score >= 0 && score <= 1 ? score : null;
}

int? _token(Object? value) {
  if (value is! num || !value.isFinite || value < 0 || value % 1 != 0) {
    return null;
  }
  return value.toInt();
}

int? _readToken(Map<String, dynamic> usage, List<List<String>> paths) {
  for (final path in paths) {
    Object? current = usage;
    for (final segment in path) {
      if (current is! Map) {
        current = null;
        break;
      }
      current = current[segment];
    }
    final token = _token(current);
    if (token != null) return token;
  }
  return null;
}

Map<String, T> _sortedMap<T>(Map<String, T> source) {
  final keys = source.keys.toList()..sort();
  return {for (final key in keys) key: source[key] as T};
}

String _percent(double? value) {
  if (value == null) return '—';
  final percent = value * 100;
  final fixed = percent == percent.roundToDouble()
      ? percent.toStringAsFixed(0)
      : percent.toStringAsFixed(1);
  return '$fixed%';
}

String _integer(int? value) {
  if (value == null) return '—';
  final digits = value.toString();
  final buffer = StringBuffer();
  for (var index = 0; index < digits.length; index++) {
    if (index > 0 && (digits.length - index) % 3 == 0) buffer.write(',');
    buffer.write(digits[index]);
  }
  return buffer.toString();
}
