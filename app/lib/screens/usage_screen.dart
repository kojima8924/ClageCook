import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../models.dart';
import '../services/api_client.dart';

class UsageScreen extends StatefulWidget {
  const UsageScreen({super.key, required this.client});

  final ClageApiClient client;

  @override
  State<UsageScreen> createState() => _UsageScreenState();
}

class _UsageScreenState extends State<UsageScreen> {
  UsageTelemetrySnapshot? _snapshot;
  bool _loading = false;
  String _error = '';
  String? _reconcilingRequestId;

  @override
  void initState() {
    super.initState();
    unawaited(_load());
  }

  Future<void> _load() async {
    if (_loading) return;
    setState(() {
      _loading = true;
      _error = '';
    });
    try {
      final snapshot = await widget.client.usageTelemetry();
      if (!mounted) return;
      setState(() {
        _snapshot = snapshot;
        _loading = false;
      });
    } catch (error) {
      if (!mounted) return;
      setState(() {
        _error = '利用状況を取得できませんでした: $error';
        _loading = false;
      });
    }
  }

  Future<void> _releaseReservation(BudgetReservation reservation) async {
    if (_reconcilingRequestId != null) return;
    final noteController = TextEditingController();
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('照合待ち予約を解除'),
        content: SingleChildScrollView(
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              const Text(
                'Providerの管理画面または請求記録を確認し、保存済みusage以外の追加請求がない場合だけ解除してください。'
                'この操作は予約額を課金実績として確定するものではありません。',
              ),
              const SizedBox(height: 12),
              Text('request: ${reservation.requestId}'),
              Text('予約額: \$${reservation.amountUsd ?? '不明'}'),
              const SizedBox(height: 12),
              TextField(
                controller: noteController,
                maxLength: 200,
                decoration: const InputDecoration(
                  labelText: '確認メモ（任意）',
                  hintText: '例: Provider dashboardを確認',
                  border: OutlineInputBorder(),
                ),
              ),
            ],
          ),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context, false),
            child: const Text('キャンセル'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(context, true),
            child: const Text('確認済みとして解除'),
          ),
        ],
      ),
    );
    final note = noteController.text.trim();
    noteController.dispose();
    if (confirmed != true || !mounted) return;
    setState(() {
      _reconcilingRequestId = reservation.requestId;
      _error = '';
    });
    try {
      await widget.client.releaseBudgetReconciliation(
        requestId: reservation.requestId,
        note: note,
      );
      await _load();
    } catch (error) {
      if (!mounted) return;
      setState(() => _error = '予算照合を解除できませんでした: $error');
    } finally {
      if (mounted) setState(() => _reconcilingRequestId = null);
    }
  }

  @override
  Widget build(BuildContext context) {
    final snapshot = _snapshot;
    return Scaffold(
      appBar: AppBar(
        title: const Text('利用状況と予算'),
        actions: [
          IconButton(
            tooltip: '再読込',
            onPressed: _loading ? null : _load,
            icon: const Icon(Icons.refresh),
          ),
        ],
      ),
      body: RefreshIndicator(
        onRefresh: _load,
        child: ListView(
          padding: const EdgeInsets.fromLTRB(16, 12, 16, 32),
          children: [
            if (_loading) const LinearProgressIndicator(minHeight: 2),
            if (_error.isNotEmpty) ...[
              _ErrorCard(message: _error, onRetry: _load),
              const SizedBox(height: 12),
            ],
            if (snapshot == null && !_loading && _error.isEmpty)
              const Center(child: Text('利用状況はまだありません')),
            if (snapshot != null) ...[
              _ScopeCard(snapshot: snapshot),
              const SizedBox(height: 12),
              // 端末内モード(Direct BYOK)では予算・組織管理APIの数値が存在しない。
              // 「無効」だけのカードを並べても読み取れる情報がないので、
              // 代わりに何が見えて何が見えないかを1枚で説明する。
              if (_localOnly(snapshot)) ...[
                const _LocalOnlyUsageCard(),
                const SizedBox(height: 12),
              ] else ...[
                _BudgetCard(
                  finance: snapshot.finance,
                  reconcilingRequestId: _reconcilingRequestId,
                  onRelease: _releaseReservation,
                ),
                const SizedBox(height: 12),
                _AdminTelemetryCard(admin: snapshot.admin),
                const SizedBox(height: 12),
              ],
              for (final provider in snapshot.providers) ...[
                _ProviderCard(provider: provider),
                const SizedBox(height: 12),
              ],
              _LimitationsCard(limitations: snapshot.limitations),
            ],
          ],
        ),
      ),
    );
  }
}

/// Direct BYOK(端末内モード)では、providers/finance/adminが空で返る。
bool _localOnly(UsageTelemetrySnapshot snapshot) =>
    snapshot.providers.isEmpty &&
    !snapshot.finance.configured &&
    !snapshot.admin.enabled;

class _LocalOnlyUsageCard extends StatelessWidget {
  const _LocalOnlyUsageCard();

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Row(
              children: [
                const Icon(Icons.phone_android),
                const SizedBox(width: 8),
                Expanded(
                  child: Text(
                    '端末内モードの表示範囲',
                    style: theme.textTheme.titleMedium,
                  ),
                ),
              ],
            ),
            const SizedBox(height: 8),
            const Text('この画面には、端末に保存された会話から集計した実測値だけを表示します。'),
            const SizedBox(height: 6),
            Text(
              '金額の上限管理(ローカル予算)と組織管理APIの読み取りは、開発用サーバーへ接続した場合の機能です。'
              '残高・請求額は各社のコンソールで確認してください。',
              style: theme.textTheme.bodySmall,
            ),
          ],
        ),
      ),
    );
  }
}

class _AdminTelemetryCard extends StatelessWidget {
  const _AdminTelemetryCard({required this.admin});

  final AdminTelemetrySnapshot admin;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Row(
              children: [
                const Icon(Icons.cloud_sync_outlined),
                const SizedBox(width: 8),
                Expanded(
                  child: Text('組織管理API', style: theme.textTheme.titleMedium),
                ),
                Chip(
                  label: Text(admin.enabled ? '有効' : '未接続'),
                  visualDensity: VisualDensity.compact,
                ),
              ],
            ),
            const SizedBox(height: 8),
            if (!admin.enabled)
              Text(
                '既定では外部通信しません。別管理キーと認証を設定した場合だけ、'
                '組織usage・cost・取得可能なcreditを読み取ります。',
                style: theme.textTheme.bodySmall,
              )
            else ...[
              Text(
                '直近${admin.lookbackDays}日 · 読み取り専用'
                '${admin.cacheHit ? ' · cache' : ''}',
                style: theme.textTheme.bodySmall,
              ),
              const SizedBox(height: 10),
              for (final provider in admin.providers) ...[
                _AdminProviderRow(provider: provider),
                if (provider != admin.providers.last) const Divider(height: 20),
              ],
              if (admin.generatedAt.isNotEmpty) ...[
                const SizedBox(height: 6),
                Text(
                  '取得: ${_localTime(admin.generatedAt)}',
                  style: theme.textTheme.bodySmall,
                ),
              ],
            ],
          ],
        ),
      ),
    );
  }
}

class _AdminProviderRow extends StatelessWidget {
  const _AdminProviderRow({required this.provider});

  final AdminProviderTelemetry provider;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Row(
          children: [
            Expanded(child: Text(provider.label)),
            Text(
              _adminStatus(provider.status),
              style: theme.textTheme.labelMedium?.copyWith(
                color: provider.status == 'ok'
                    ? theme.colorScheme.primary
                    : theme.colorScheme.onSurfaceVariant,
              ),
            ),
          ],
        ),
        if (provider.window.hasData) ...[
          const SizedBox(height: 6),
          _AdminProviderWindowSummary(provider: provider),
        ],
        if (provider.status == 'unsupported')
          Text(
            'Developer APIキーからの集計取得には未対応です。AI Studioで確認します。',
            style: theme.textTheme.bodySmall,
          )
        else if (provider.status == 'not_configured')
          Text('別管理キーは未設定です。', style: theme.textTheme.bodySmall)
        else if (provider.status == 'disabled')
          Text('管理telemetryは無効です。', style: theme.textTheme.bodySmall)
        else ...[
          const SizedBox(height: 6),
          Wrap(
            spacing: 8,
            runSpacing: 8,
            children: [
              if (provider.costUsd != null)
                _MoneyMetric(label: '期間cost', value: provider.costUsd),
              if (provider.creditBalanceUsd != null)
                _MoneyMetric(
                  label: '報告credit残高*',
                  value: provider.creditBalanceUsd,
                ),
              if (provider.currentInvoiceUsd != null)
                _MoneyMetric(
                  label: '当期invoice見込',
                  value: provider.currentInvoiceUsd,
                ),
              if (provider.softLimitUsd != null)
                _MoneyMetric(label: 'Soft上限', value: provider.softLimitUsd),
              if (provider.hardLimitUsd != null)
                _MoneyMetric(label: 'Hard上限', value: provider.hardLimitUsd),
              if (provider.usage['requests'] != null)
                _CountMetric(label: '組織呼出', value: provider.usage['requests']),
              if (provider.usage['input_tokens'] != null)
                _CountMetric(
                  label: '組織Input',
                  value: provider.usage['input_tokens'],
                ),
              if (provider.usage['output_tokens'] != null)
                _CountMetric(
                  label: '組織Output',
                  value: provider.usage['output_tokens'],
                ),
            ],
          ),
          if (provider.balanceSignConvention == 'provider_reported') ...[
            const SizedBox(height: 5),
            Text('* xAIが返した符号を変換せず表示しています。', style: theme.textTheme.bodySmall),
          ],
          if (provider.errorCodes.isNotEmpty) ...[
            const SizedBox(height: 5),
            Text(
              '一部取得失敗: ${provider.errorCodes.map(_adminError).join('、')}',
              style: theme.textTheme.bodySmall?.copyWith(
                color: theme.colorScheme.error,
              ),
            ),
          ],
        ],
      ],
    );
  }
}

class _AdminProviderWindowSummary extends StatelessWidget {
  const _AdminProviderWindowSummary({required this.provider});

  final AdminProviderTelemetry provider;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final window = provider.window;
    final exact = window.exactBudgetWindow;
    final utcDailyBucket =
        provider.name == 'claude' && window.exactBudgetWindow == false;
    final effectiveRange = _utcRange(window.startingAt, window.endingAt);
    final requestedRange = _utcRange(
      window.requestedStartingAt,
      window.requestedEndingAt,
    );
    return DecoratedBox(
      decoration: BoxDecoration(
        color: exact == false
            ? theme.colorScheme.tertiaryContainer
            : theme.colorScheme.surfaceContainerHighest,
        borderRadius: BorderRadius.circular(8),
      ),
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 8),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            if (exact == false) ...[
              Row(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  const Icon(Icons.info_outline, size: 18),
                  const SizedBox(width: 6),
                  Expanded(
                    child: Text(
                      utcDailyBucket
                          ? 'UTC日次bucketで集計され、ローカル予算期間とは一致しません。'
                          : 'Providerのbucketで集計され、ローカル予算期間とは一致しません。',
                      style: theme.textTheme.bodySmall?.copyWith(
                        fontWeight: FontWeight.w600,
                      ),
                    ),
                  ),
                ],
              ),
              const SizedBox(height: 4),
            ],
            Text(
              exact == true
                  ? '集計期間（予算期間と一致）: $effectiveRange'
                  : '実効期間: $effectiveRange',
              style: theme.textTheme.bodySmall,
            ),
            if (exact == false) ...[
              Text('要求予算期間: $requestedRange', style: theme.textTheme.bodySmall),
              Text(
                '完全集計済み境界（complete-through）: '
                '${_utcTime(window.completeThrough)}',
                style: theme.textTheme.bodySmall,
              ),
            ],
          ],
        ),
      ),
    );
  }
}

class _ScopeCard extends StatelessWidget {
  const _ScopeCard({required this.snapshot});

  final UsageTelemetrySnapshot snapshot;

  @override
  Widget build(BuildContext context) => Card(
    child: Padding(
      padding: const EdgeInsets.all(16),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Text('ローカル実績', style: Theme.of(context).textTheme.titleMedium),
          const SizedBox(height: 8),
          Text('${snapshot.conversationCount}会話 · ${snapshot.turnCount}ターン'),
          const SizedBox(height: 4),
          Text(
            'Clage Cookが保存できたProvider応答だけを集計しています。',
            style: Theme.of(context).textTheme.bodySmall,
          ),
          if (snapshot.generatedAt.isNotEmpty) ...[
            const SizedBox(height: 4),
            Text(
              '更新: ${_localTime(snapshot.generatedAt)}',
              style: Theme.of(context).textTheme.bodySmall,
            ),
          ],
        ],
      ),
    ),
  );
}

class _BudgetCard extends StatelessWidget {
  const _BudgetCard({
    required this.finance,
    required this.reconcilingRequestId,
    required this.onRelease,
  });

  final BudgetSnapshot finance;
  final String? reconcilingRequestId;
  final ValueChanged<BudgetReservation> onRelease;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Row(
              children: [
                const Icon(Icons.account_balance_wallet_outlined),
                const SizedBox(width: 8),
                Expanded(
                  child: Text('ローカル予算', style: theme.textTheme.titleMedium),
                ),
                Chip(
                  label: Text(finance.configured ? '有効' : '未設定'),
                  visualDensity: VisualDensity.compact,
                ),
              ],
            ),
            const SizedBox(height: 10),
            if (!finance.configured) ...[
              const Text('日次・会議単位の金額上限は設定されていません。'),
              const SizedBox(height: 4),
              Text(
                finance.priceTableLoaded
                    ? '価格表は読込済みです。送信前の金額見積りに使用できます。'
                    : '正確なmodel単価を明示した価格表を設定した場合だけ金額を計算します。',
                style: theme.textTheme.bodySmall,
              ),
            ] else ...[
              Wrap(
                spacing: 8,
                runSpacing: 8,
                children: [
                  _MoneyMetric(label: '本日確定推定', value: finance.todayActualUsd),
                  _MoneyMetric(
                    label: '有効予約（総額）',
                    value: finance.todayReservedUsd,
                  ),
                  _MoneyMetric(
                    label: '実績未反映の追加拘束',
                    value: finance.todayActiveReservationTopUpUsd,
                  ),
                  _MoneyMetric(
                    label: '本日拘束済み',
                    value: finance.todayCommittedUsd,
                  ),
                  _MoneyMetric(label: '本日残り', value: finance.todayRemainingUsd),
                  _MoneyMetric(label: '日次上限', value: finance.dailyLimitUsd),
                  _MoneyMetric(label: '1会議上限', value: finance.perRunLimitUsd),
                ],
              ),
              const SizedBox(height: 10),
              Text(
                '有効予約 ${finance.activeReservationCount}件 · '
                '未照合 ${finance.unreconciledCount}'
                '${finance.maxUnreconciledCount > 0 ? '/${finance.maxUnreconciledCount}' : ''}件 · '
                '金額不明 ${finance.unpricedRequests}件 · '
                '不明時 ${finance.unknownCostPolicy == 'block' ? '停止' : '許可'}',
              ),
              const SizedBox(height: 6),
              Text(
                '有効予約（総額）は実行開始時の予約額です。'
                '実績未反映の追加拘束は、観測済み実績と重複せず'
                '日次上限に追加算入されている差額です。',
                style: theme.textTheme.bodySmall,
              ),
            ],
            if (finance.priceVersion != null) ...[
              const SizedBox(height: 6),
              Text(
                '価格版: ${finance.priceVersion}',
                style: theme.textTheme.bodySmall,
              ),
            ],
            if (finance.activeReservations.isNotEmpty) ...[
              const SizedBox(height: 12),
              Text('有効な予算予約', style: theme.textTheme.titleSmall),
              const SizedBox(height: 6),
              for (final reservation in finance.activeReservations)
                ListTile(
                  contentPadding: EdgeInsets.zero,
                  dense: true,
                  leading: Icon(
                    reservation.state == 'reconciliation_pending'
                        ? Icons.pending_actions_outlined
                        : Icons.lock_clock_outlined,
                  ),
                  title: Text(
                    reservation.requestId,
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                  ),
                  subtitle: Text(
                    '${reservation.state} · \$${reservation.amountUsd ?? '不明'}'
                    '${reservation.createdAt.isEmpty ? '' : ' · ${_localTime(reservation.createdAt)}'}',
                  ),
                  trailing: reservation.state == 'reconciliation_pending'
                      ? TextButton(
                          onPressed: reconcilingRequestId == null
                              ? () => onRelease(reservation)
                              : null,
                          child: reconcilingRequestId == reservation.requestId
                              ? const SizedBox.square(
                                  dimension: 16,
                                  child: CircularProgressIndicator(
                                    strokeWidth: 2,
                                  ),
                                )
                              : const Text('照合'),
                        )
                      : null,
                ),
            ],
            if (finance.disclaimer.isNotEmpty) ...[
              const SizedBox(height: 8),
              Text(finance.disclaimer, style: theme.textTheme.bodySmall),
            ],
          ],
        ),
      ),
    );
  }
}

class _ProviderCard extends StatelessWidget {
  const _ProviderCard({required this.provider});

  final ProviderTelemetry provider;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final quota = provider.latestQuota;
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Row(
              children: [
                Expanded(
                  child: Text(
                    provider.label,
                    style: theme.textTheme.titleMedium,
                  ),
                ),
                Chip(
                  label: Text(provider.mode.toUpperCase()),
                  visualDensity: VisualDensity.compact,
                ),
              ],
            ),
            const SizedBox(height: 10),
            Text('今日', style: theme.textTheme.labelLarge),
            const SizedBox(height: 6),
            _UsageMetrics(period: provider.today),
            const SizedBox(height: 12),
            Text('全期間', style: theme.textTheme.labelLarge),
            const SizedBox(height: 6),
            _UsageMetrics(period: provider.allTime),
            const Divider(height: 24),
            Text('最新のrate-limit観測', style: theme.textTheme.labelLarge),
            const SizedBox(height: 6),
            if (quota.dimensions.isEmpty)
              Text(
                provider.rateLimitHeadersSupported
                    ? '実APIの成功応答を受け取ると、取得可能な残量を表示します。'
                    : '通常API応答からの共通取得には対応していません。',
                style: theme.textTheme.bodySmall,
              )
            else ...[
              for (final entry in quota.dimensions.entries)
                Padding(
                  padding: const EdgeInsets.only(bottom: 5),
                  child: Text(
                    '${_dimensionLabel(entry.key)}: '
                    '残り ${_number(entry.value.remaining)} / '
                    '上限 ${_number(entry.value.limit)}'
                    '${entry.value.reset == null ? '' : ' · reset ${entry.value.reset}'}',
                  ),
                ),
              if (quota.observedAt != null)
                Text(
                  '観測: ${_localTime(quota.observedAt!)}',
                  style: theme.textTheme.bodySmall,
                ),
            ],
            if (provider.portalUrl.isNotEmpty) ...[
              const SizedBox(height: 10),
              Align(
                alignment: Alignment.centerLeft,
                child: TextButton.icon(
                  onPressed: () async {
                    await Clipboard.setData(
                      ClipboardData(text: provider.portalUrl),
                    );
                    if (!context.mounted) return;
                    ScaffoldMessenger.of(context).showSnackBar(
                      SnackBar(
                        content: Text('${provider.label}管理画面URLをコピーしました'),
                      ),
                    );
                  },
                  icon: const Icon(Icons.content_copy_outlined),
                  label: const Text('管理画面URLをコピー'),
                ),
              ),
            ],
          ],
        ),
      ),
    );
  }
}

class _UsageMetrics extends StatelessWidget {
  const _UsageMetrics({required this.period});

  final UsagePeriod period;

  @override
  Widget build(BuildContext context) => Wrap(
    spacing: 8,
    runSpacing: 8,
    children: [
      _CountMetric(label: '呼出', value: period.observedRequests),
      _CountMetric(label: 'Input', value: period.usage['input_tokens']),
      _CountMetric(label: 'Output', value: period.usage['output_tokens']),
      _CountMetric(label: 'Total', value: period.usage['total_tokens']),
      _CountMetric(label: 'Cached', value: period.usage['cached_input_tokens']),
      _CountMetric(label: '不明', value: period.usageUnknownRequests),
    ],
  );
}

class _CountMetric extends StatelessWidget {
  const _CountMetric({required this.label, required this.value});

  final String label;
  final int? value;

  @override
  Widget build(BuildContext context) => Container(
    constraints: const BoxConstraints(minWidth: 92),
    padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 8),
    decoration: BoxDecoration(
      color: Theme.of(context).colorScheme.surfaceContainerHigh,
      borderRadius: BorderRadius.circular(10),
    ),
    child: Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(label, style: Theme.of(context).textTheme.labelSmall),
        Text(_number(value), style: Theme.of(context).textTheme.titleSmall),
      ],
    ),
  );
}

class _MoneyMetric extends StatelessWidget {
  const _MoneyMetric({required this.label, required this.value});

  final String label;
  final String? value;

  @override
  Widget build(BuildContext context) => Container(
    constraints: const BoxConstraints(minWidth: 135),
    padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 8),
    decoration: BoxDecoration(
      color: Theme.of(context).colorScheme.surfaceContainerHigh,
      borderRadius: BorderRadius.circular(10),
    ),
    child: Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(label, style: Theme.of(context).textTheme.labelSmall),
        Text(
          value == null ? '—' : '\$$value',
          style: Theme.of(context).textTheme.titleSmall,
        ),
      ],
    ),
  );
}

class _LimitationsCard extends StatelessWidget {
  const _LimitationsCard({required this.limitations});

  final List<String> limitations;

  @override
  Widget build(BuildContext context) => Card(
    child: Padding(
      padding: const EdgeInsets.all(16),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Text('集計の限界', style: Theme.of(context).textTheme.titleMedium),
          const SizedBox(height: 8),
          for (final item in limitations)
            Padding(
              padding: const EdgeInsets.only(bottom: 5),
              child: Text('• $item'),
            ),
        ],
      ),
    ),
  );
}

class _ErrorCard extends StatelessWidget {
  const _ErrorCard({required this.message, required this.onRetry});

  final String message;
  final VoidCallback onRetry;

  @override
  Widget build(BuildContext context) => Card(
    color: Theme.of(context).colorScheme.errorContainer,
    child: ListTile(
      leading: const Icon(Icons.error_outline),
      title: Text(message),
      trailing: IconButton(
        tooltip: '再試行',
        onPressed: onRetry,
        icon: const Icon(Icons.refresh),
      ),
    ),
  );
}

String _number(int? value) {
  if (value == null) return '—';
  final digits = value.toString();
  final output = StringBuffer();
  for (var index = 0; index < digits.length; index++) {
    if (index > 0 && (digits.length - index) % 3 == 0) output.write(',');
    output.write(digits[index]);
  }
  return output.toString();
}

String _dimensionLabel(String value) => switch (value) {
  'requests' => 'Requests',
  'tokens' => 'Tokens',
  'input_tokens' => 'Input tokens',
  'output_tokens' => 'Output tokens',
  _ => value,
};

String _adminStatus(String value) => switch (value) {
  'ok' => '取得済み',
  'partial' => '一部取得',
  'error' => '取得失敗',
  'not_configured' => '未設定',
  'unsupported' => 'Portalのみ',
  'disabled' => '無効',
  _ => value,
};

String _adminError(String value) => switch (value) {
  'unauthorized' => '認証失敗',
  'forbidden' => '権限不足',
  'not_available' => 'API対象外',
  'rate_limited' => 'rate limit',
  'network_error' => '通信失敗',
  'team_id_required' => 'team IDが必要',
  'request_rejected' => '要求拒否',
  'invalid_response' => '応答形式不正',
  'pagination_limit' => '取得上限到達',
  _ => value,
};

String _localTime(String value) {
  final parsed = DateTime.tryParse(value)?.toLocal();
  if (parsed == null) return value;
  String two(int number) => number.toString().padLeft(2, '0');
  return '${parsed.year}/${two(parsed.month)}/${two(parsed.day)} '
      '${two(parsed.hour)}:${two(parsed.minute)}';
}

String _utcRange(String? startingAt, String? endingAt) =>
    '${_utcTime(startingAt)} → ${_utcTime(endingAt)}';

String _utcTime(String? value) {
  if (value == null || value.isEmpty) return '不明';
  final parsed = DateTime.tryParse(value)?.toUtc();
  if (parsed == null) return value;
  String two(int number) => number.toString().padLeft(2, '0');
  return '${parsed.year}/${two(parsed.month)}/${two(parsed.day)} '
      '${two(parsed.hour)}:${two(parsed.minute)} UTC';
}
