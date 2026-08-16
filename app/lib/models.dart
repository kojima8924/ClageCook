class ConnectionSettings {
  const ConnectionSettings({
    this.baseUrl = 'http://127.0.0.1:8000',
    this.token = '',
  });

  final String baseUrl;
  final String token;

  ConnectionSettings copyWith({String? baseUrl, String? token}) =>
      ConnectionSettings(
        baseUrl: baseUrl ?? this.baseUrl,
        token: token ?? this.token,
      );
}

class ProviderStatus {
  const ProviderStatus({
    required this.name,
    required this.label,
    required this.configured,
    required this.mode,
    required this.models,
  });

  factory ProviderStatus.fromJson(Map<String, dynamic> json) => ProviderStatus(
    name: json['name']?.toString() ?? '',
    label: json['label']?.toString() ?? '',
    configured: json['configured'] == true,
    mode: json['mode']?.toString() ?? 'mock',
    models:
        (json['models'] as Map?)?.map(
          (key, value) => MapEntry(key.toString(), value.toString()),
        ) ??
        const {},
  );

  final String name;
  final String label;
  final bool configured;
  final String mode;
  final Map<String, String> models;
}

class RuntimeModelSettings {
  const RuntimeModelSettings({
    required this.revision,
    required this.writable,
    required this.synthesizerProvider,
    required this.effectiveSynthesizerModels,
    required this.catalog,
  });

  factory RuntimeModelSettings.fromJson(Map<String, dynamic> json) {
    final rawCatalog = json['catalog'];
    final catalog = <String, List<String>>{};
    if (rawCatalog is Map) {
      for (final entry in rawCatalog.entries) {
        catalog[entry.key.toString()] = _stringList(entry.value);
      }
    }
    return RuntimeModelSettings(
      revision: _asInt(json['revision']),
      writable: json['writable'] == true,
      synthesizerProvider: json['synthesizer_provider']?.toString() ?? 'auto',
      effectiveSynthesizerModels:
          (json['effective_synthesizer_models'] as Map?)?.map(
            (key, value) => MapEntry(key.toString(), value.toString()),
          ) ??
          const {},
      catalog: catalog,
    );
  }

  final int revision;
  final bool writable;
  final String synthesizerProvider;
  final Map<String, String> effectiveSynthesizerModels;
  final Map<String, List<String>> catalog;
}

class ServerWebSearchSettings {
  const ServerWebSearchSettings({
    this.enabled = false,
    this.defaultEnabled = false,
    this.maxUses = 0,
    this.strictTotalLimit = false,
  });

  factory ServerWebSearchSettings.fromJson(Map<String, dynamic> json) =>
      ServerWebSearchSettings(
        enabled: json['enabled'] == true,
        defaultEnabled: json['default'] == true,
        maxUses: _asInt(json['max_uses']),
        strictTotalLimit: json['strict_total_limit'] == true,
      );

  final bool enabled;
  final bool defaultEnabled;
  final int maxUses;
  final bool strictTotalLimit;
}

class ServerSettings {
  const ServerSettings({
    required this.mode,
    required this.providers,
    required this.activeWorkers,
    required this.synthesizer,
    required this.authRequired,
    this.liveApiEnabled = false,
    this.webSearch = const ServerWebSearchSettings(),
    this.runtimeSettings = const RuntimeModelSettings(
      revision: 0,
      writable: false,
      synthesizerProvider: 'auto',
      effectiveSynthesizerModels: {},
      catalog: {},
    ),
  });

  factory ServerSettings.fromJson(Map<String, dynamic> json) => ServerSettings(
    mode: json['mode']?.toString() ?? '',
    providers: _mapList(json['providers'], ProviderStatus.fromJson),
    activeWorkers: _stringList(json['active_workers']),
    synthesizer: json['synthesizer']?.toString() ?? '',
    authRequired: json['auth_required'] == true,
    liveApiEnabled: json['live_api_enabled'] == true,
    webSearch: json['web_search'] is Map
        ? ServerWebSearchSettings.fromJson(
            Map<String, dynamic>.from(json['web_search'] as Map),
          )
        : const ServerWebSearchSettings(),
    runtimeSettings: json['runtime_settings'] is Map
        ? RuntimeModelSettings.fromJson(
            Map<String, dynamic>.from(json['runtime_settings'] as Map),
          )
        : const RuntimeModelSettings(
            revision: 0,
            writable: false,
            synthesizerProvider: 'auto',
            effectiveSynthesizerModels: {},
            catalog: {},
          ),
  );

  final String mode;
  final List<ProviderStatus> providers;
  final List<String> activeWorkers;
  final String synthesizer;
  final bool authRequired;
  final bool liveApiEnabled;
  final ServerWebSearchSettings webSearch;
  final RuntimeModelSettings runtimeSettings;
}

class CitationRecord {
  const CitationRecord({
    required this.url,
    required this.title,
    this.startIndex,
    this.endIndex,
  });

  factory CitationRecord.fromJson(Map<String, dynamic> json) => CitationRecord(
    url: json['url']?.toString() ?? '',
    title: json['title']?.toString() ?? '',
    startIndex: _nullableInt(json['start_index']),
    endIndex: _nullableInt(json['end_index']),
  );

  final String url;
  final String title;
  final int? startIndex;
  final int? endIndex;
}

class PolicyFinding {
  const PolicyFinding({
    required this.ruleId,
    required this.label,
    required this.severity,
    required this.start,
    required this.end,
  });

  factory PolicyFinding.fromJson(Map<String, dynamic> json) => PolicyFinding(
    ruleId: json['rule_id']?.toString() ?? '',
    label: json['label']?.toString() ?? '機密情報らしい文字列',
    severity: json['severity']?.toString() ?? 'block',
    start: _asInt(json['start']),
    end: _asInt(json['end']),
  );

  final String ruleId;
  final String label;
  final String severity;
  final int start;
  final int end;
}

/// ポリシー検査の判定。未知の値は必ず [block] へ倒す(fail-closed)。
enum PolicyAction {
  allow('allow'),
  confirm('confirm'),
  block('block');

  const PolicyAction(this.wireName);

  final String wireName;

  /// 未知・欠落は送信を止める側へ倒す。将来サーバーが新しい判定名を返しても、
  /// 「知らないから送ってよい」にはしない。
  static PolicyAction parse(Object? raw) => switch (raw?.toString()) {
    'allow' => PolicyAction.allow,
    'confirm' => PolicyAction.confirm,
    _ => PolicyAction.block,
  };
}

class PolicyScanResult {
  const PolicyScanResult({
    required this.action,
    required this.findings,
    required this.redactedText,
    required this.disclaimer,
    this.version = '',
  });

  factory PolicyScanResult.fromJson(Map<String, dynamic> json) =>
      PolicyScanResult(
        action: PolicyAction.parse(json['action']),
        findings: _mapList(json['findings'], PolicyFinding.fromJson),
        redactedText: json['redacted_text']?.toString() ?? '',
        disclaimer: json['disclaimer']?.toString() ?? '',
        version: json['version']?.toString() ?? '',
      );

  final PolicyAction action;
  final List<PolicyFinding> findings;
  final String redactedText;
  final String disclaimer;
  final String version;

  bool get blocked => action == PolicyAction.block;

  /// 送信前に利用者の確認が要る状態。
  bool get needsConfirmation => action == PolicyAction.confirm;
}

/// 会議参加者の実行形態。未知の値は課金しない側([mock])へ倒す。
enum ParticipantMode {
  live('live'),
  mock('mock'),
  disabled('disabled'),
  unknown('');

  const ParticipantMode(this.wireName);

  final String wireName;

  static ParticipantMode parse(Object? raw) => switch (raw?.toString()) {
    'live' => ParticipantMode.live,
    'mock' => ParticipantMode.mock,
    'disabled' => ParticipantMode.disabled,
    null => ParticipantMode.mock,
    _ => ParticipantMode.unknown,
  };
}

class RunPlanParticipant {
  const RunPlanParticipant({
    required this.name,
    required this.label,
    required this.mode,
    required this.model,
    required this.billable,
    required this.maxCalls,
    this.enabled = true,
    this.maxOutputTokens = 0,
    this.reasoning = const {},
  });

  factory RunPlanParticipant.fromJson(Map<String, dynamic> json) =>
      RunPlanParticipant(
        name: json['name']?.toString() ?? '',
        label: json['label']?.toString() ?? '',
        mode: ParticipantMode.parse(json['mode']),
        model: json['model']?.toString() ?? '',
        billable: json['billable'] == true,
        maxCalls: _asInt(json['max_calls']),
        enabled: json['enabled'] != false,
        maxOutputTokens: _asInt(json['max_output_tokens']),
        reasoning: _dynamicMapOrNull(json['reasoning']) ?? const {},
      );

  final String name;
  final String label;
  final ParticipantMode mode;
  final String model;
  final bool billable;
  final int maxCalls;
  final bool enabled;

  /// 1 callあたりの最大出力token。planの送出側は以前から出していたが、
  /// 読み捨てていたため計画画面から参照できなかった。
  final int maxOutputTokens;

  /// 実効推論エフォートの監査情報。
  final Map<String, dynamic> reasoning;
}

class RunPlanWarning {
  const RunPlanWarning({required this.code, required this.message});

  factory RunPlanWarning.fromJson(Map<String, dynamic> json) => RunPlanWarning(
    code: json['code']?.toString() ?? '',
    message: json['message']?.toString() ?? '',
  );

  final String code;
  final String message;
}

class RetryEnvelope {
  const RetryEnvelope({
    this.configuredRetriesPerLiveCall = 0,
    this.liveInitialCalls = 0,
    this.additionalHttpAttempts = 0,
    this.totalProviderExecutions = 0,
    this.maxOutputTokens = 0,
    this.disclaimer = '',
  });

  factory RetryEnvelope.fromJson(Map<String, dynamic> json) => RetryEnvelope(
    configuredRetriesPerLiveCall: _asInt(
      json['configured_retries_per_live_call'],
    ),
    liveInitialCalls: _asInt(json['live_initial_calls']),
    additionalHttpAttempts: _asInt(json['additional_http_attempts']),
    totalProviderExecutions: _asInt(json['total_provider_executions']),
    maxOutputTokens: _asInt(json['max_output_tokens']),
    disclaimer: json['disclaimer']?.toString() ?? '',
  );

  final int configuredRetriesPerLiveCall;
  final int liveInitialCalls;
  final int additionalHttpAttempts;
  final int totalProviderExecutions;
  final int maxOutputTokens;
  final String disclaimer;

  bool get hasRetries => additionalHttpAttempts > 0;
}

class InputEnvelope {
  const InputEnvelope({
    this.unit = 'utf8_bytes',
    this.history = 0,
    this.answerPerCall = 0,
    this.answersTotal = 0,
    this.debateTotal = 0,
    this.synthesis = 0,
    this.total = 0,
    this.liveInitialTotal = 0,
    this.liveWithRetries = 0,
    this.totalWithRetries = 0,
    this.tokenCountEstimated = false,
    this.disclaimer = '',
  });

  factory InputEnvelope.fromJson(Map<String, dynamic> json) => InputEnvelope(
    unit: json['unit']?.toString() ?? 'utf8_bytes',
    history: _asInt(json['history']),
    answerPerCall: _asInt(json['answer_per_call']),
    answersTotal: _asInt(json['answers_total']),
    debateTotal: _asInt(json['debate_total']),
    synthesis: _asInt(json['synthesis']),
    total: _asInt(json['total']),
    liveInitialTotal: _asInt(json['live_initial_total']),
    liveWithRetries: _asInt(json['live_with_retries']),
    totalWithRetries: _asInt(json['total_with_retries']),
    tokenCountEstimated: json['token_count_estimated'] == true,
    disclaimer: json['disclaimer']?.toString() ?? '',
  );

  final String unit;
  final int history;
  final int answerPerCall;
  final int answersTotal;
  final int debateTotal;
  final int synthesis;
  final int total;
  final int liveInitialTotal;
  final int liveWithRetries;
  final int totalWithRetries;
  final bool tokenCountEstimated;
  final String disclaimer;
}

class RunPlan {
  const RunPlan({
    required this.allowed,
    required this.blockReasons,
    required this.billable,
    required this.mode,
    required this.providers,
    required this.synthesizer,
    required this.calls,
    required this.maxOutputTokens,
    required this.retryEnvelope,
    required this.inputEnvelope,
    required this.policy,
    required this.warnings,
    this.costEstimate = const RunCostEstimate(),
    this.budget = const BudgetSnapshot(),
  });

  factory RunPlan.fromJson(Map<String, dynamic> json) {
    final rawSynthesizer = json['synthesizer'];
    final rawPolicy = json['policy'];
    final rawRetryEnvelope = json['retry_envelope'];
    final rawInputEnvelope = json['input_envelope'];
    return RunPlan(
      allowed: json['allowed'] == true,
      blockReasons: _stringList(json['block_reasons']),
      billable: json['billable'] == true,
      mode: json['mode']?.toString() ?? 'mock',
      providers: _mapList(json['providers'], RunPlanParticipant.fromJson),
      synthesizer: rawSynthesizer is Map
          ? RunPlanParticipant.fromJson(
              Map<String, dynamic>.from(rawSynthesizer),
            )
          : const RunPlanParticipant(
              name: 'synthesizer',
              label: 'Synthesizer',
              mode: ParticipantMode.mock,
              model: 'mock',
              billable: false,
              maxCalls: 0,
              enabled: false,
            ),
      calls: _intMap(json['calls']),
      maxOutputTokens: _intMap(json['max_output_tokens']),
      retryEnvelope: rawRetryEnvelope is Map
          ? RetryEnvelope.fromJson(Map<String, dynamic>.from(rawRetryEnvelope))
          : const RetryEnvelope(),
      inputEnvelope: rawInputEnvelope is Map
          ? InputEnvelope.fromJson(Map<String, dynamic>.from(rawInputEnvelope))
          : const InputEnvelope(),
      policy: rawPolicy is Map
          ? PolicyScanResult.fromJson(Map<String, dynamic>.from(rawPolicy))
          : const PolicyScanResult(
              action: PolicyAction.block,
              findings: [],
              redactedText: '',
              disclaimer: 'ポリシー検査結果を取得できませんでした。',
            ),
      warnings: _mapList(json['warnings'], RunPlanWarning.fromJson),
      costEstimate: json['cost_estimate'] is Map
          ? RunCostEstimate.fromJson(
              Map<String, dynamic>.from(json['cost_estimate'] as Map),
            )
          : const RunCostEstimate(),
      budget: json['budget'] is Map
          ? BudgetSnapshot.fromJson(
              Map<String, dynamic>.from(json['budget'] as Map),
            )
          : const BudgetSnapshot(),
    );
  }

  final bool allowed;
  final List<String> blockReasons;
  final bool billable;
  final String mode;
  final List<RunPlanParticipant> providers;
  final RunPlanParticipant synthesizer;
  final Map<String, int> calls;
  final Map<String, int> maxOutputTokens;
  final RetryEnvelope retryEnvelope;
  final InputEnvelope inputEnvelope;
  final PolicyScanResult policy;
  final List<RunPlanWarning> warnings;
  final RunCostEstimate costEstimate;
  final BudgetSnapshot budget;

  List<RunPlanParticipant> get billableParticipants => [
    ...providers.where((provider) => provider.billable),
    if (synthesizer.enabled && synthesizer.billable) synthesizer,
  ];

  int get maxLiveCalls => billableParticipants.fold(
    0,
    (total, participant) => total + participant.maxCalls,
  );
}

class RunCostEstimate {
  const RunCostEstimate({
    this.available = false,
    this.complete = false,
    this.totalMicros,
    this.totalUsd,
    this.priceVersion,
    this.method = '',
  });

  factory RunCostEstimate.fromJson(Map<String, dynamic> json) =>
      RunCostEstimate(
        available: json['available'] == true,
        complete: json['complete'] == true,
        totalMicros: _nullableInt(json['total_micros']),
        totalUsd: _textOrNull(json['total_usd']),
        priceVersion: _textOrNull(json['price_version']),
        method: json['method']?.toString() ?? '',
      );

  final bool available;
  final bool complete;
  final int? totalMicros;
  final String? totalUsd;
  final String? priceVersion;
  final String method;
}

class BudgetSnapshot {
  const BudgetSnapshot({
    this.configured = false,
    this.allowed = true,
    this.unknownCostPolicy = 'block',
    this.runEstimateUsd,
    this.perRunLimitUsd,
    this.dailyLimitUsd,
    this.todayActualUsd,
    this.todayReservedUsd,
    this.todayActiveReservationTopUpUsd,
    this.todayCommittedUsd,
    this.todayRemainingUsd,
    this.day = '',
    this.unpricedRequests = 0,
    this.priceTableLoaded = false,
    this.priceVersion,
    this.activeReservationCount = 0,
    this.unreconciledCount = 0,
    this.maxUnreconciledCount = 0,
    this.activeReservations = const [],
    this.disclaimer = '',
  });

  factory BudgetSnapshot.fromJson(Map<String, dynamic> json) {
    final limits = _dynamicMapOrNull(json['limits']) ?? const {};
    final today = _dynamicMapOrNull(json['today']) ?? const {};
    final price = _dynamicMapOrNull(json['price_table']) ?? const {};
    final backlog =
        _dynamicMapOrNull(json['reconciliation_backlog']) ?? const {};
    return BudgetSnapshot(
      configured: json['configured'] == true,
      allowed: json['allowed'] != false,
      unknownCostPolicy: json['unknown_cost_policy']?.toString() ?? 'block',
      runEstimateUsd: _textOrNull(json['run_estimate_usd']),
      perRunLimitUsd: _textOrNull(limits['per_run_usd']),
      dailyLimitUsd: _textOrNull(limits['daily_usd']),
      todayActualUsd: _textOrNull(today['actual_estimated_usd']),
      todayReservedUsd: _textOrNull(today['active_reservations_usd']),
      todayActiveReservationTopUpUsd: _textOrNull(
        today['active_reservation_top_up_usd'],
      ),
      todayCommittedUsd: _textOrNull(today['committed_usd']),
      todayRemainingUsd: _textOrNull(today['remaining_usd']),
      day: today['day']?.toString() ?? '',
      unpricedRequests: _asInt(today['unpriced_requests']),
      priceTableLoaded: price['loaded'] == true,
      priceVersion: _textOrNull(price['version']),
      activeReservationCount: json['active_reservations'] is List
          ? (json['active_reservations'] as List).length
          : 0,
      unreconciledCount: _asInt(backlog['count']),
      maxUnreconciledCount: _asInt(backlog['max_count']),
      activeReservations: _mapList(
        json['active_reservations'],
        BudgetReservation.fromJson,
      ).where((item) => item.requestId.isNotEmpty).toList(growable: false),
      disclaimer: json['disclaimer']?.toString() ?? '',
    );
  }

  final bool configured;
  final bool allowed;
  final String unknownCostPolicy;
  final String? runEstimateUsd;
  final String? perRunLimitUsd;
  final String? dailyLimitUsd;
  final String? todayActualUsd;
  final String? todayReservedUsd;
  final String? todayActiveReservationTopUpUsd;
  final String? todayCommittedUsd;
  final String? todayRemainingUsd;
  final String day;
  final int unpricedRequests;
  final bool priceTableLoaded;
  final String? priceVersion;
  final int activeReservationCount;
  final int unreconciledCount;
  final int maxUnreconciledCount;
  final List<BudgetReservation> activeReservations;
  final String disclaimer;
}

class BudgetReservation {
  const BudgetReservation({
    required this.requestId,
    required this.state,
    required this.createdAt,
    this.amountUsd,
    this.reason = '',
  });

  factory BudgetReservation.fromJson(Map<String, dynamic> json) =>
      BudgetReservation(
        requestId: json['request_id']?.toString() ?? '',
        state: json['state']?.toString() ?? '',
        createdAt: json['created_at']?.toString() ?? '',
        amountUsd: _textOrNull(json['amount_usd']),
        reason: json['reconciliation_reason']?.toString() ?? '',
      );

  final String requestId;
  final String state;
  final String createdAt;
  final String? amountUsd;
  final String reason;
}

class UsagePeriod {
  const UsagePeriod({
    this.observedRequests = 0,
    this.usageUnknownRequests = 0,
    this.usage = const {},
  });

  factory UsagePeriod.fromJson(Map<String, dynamic> json) => UsagePeriod(
    observedRequests: _asInt(json['observed_requests']),
    usageUnknownRequests: _asInt(json['usage_unknown_requests']),
    usage: _intMap(json['usage']),
  );

  final int observedRequests;
  final int usageUnknownRequests;
  final Map<String, int> usage;
}

class QuotaDimension {
  const QuotaDimension({this.limit, this.remaining, this.reset});

  factory QuotaDimension.fromJson(Map<String, dynamic> json) => QuotaDimension(
    limit: _nullableInt(json['limit']),
    remaining: _nullableInt(json['remaining']),
    reset: _textOrNull(json['reset']),
  );

  final int? limit;
  final int? remaining;
  final String? reset;
}

class QuotaSnapshot {
  const QuotaSnapshot({
    this.observedAt,
    this.dimensions = const {},
    this.retryAfterSeconds,
  });

  factory QuotaSnapshot.fromJson(Map<String, dynamic> json) {
    final raw = json['dimensions'];
    final dimensions = <String, QuotaDimension>{};
    if (raw is Map) {
      for (final entry in raw.entries) {
        if (entry.value is Map) {
          dimensions[entry.key.toString()] = QuotaDimension.fromJson(
            Map<String, dynamic>.from(entry.value as Map),
          );
        }
      }
    }
    return QuotaSnapshot(
      observedAt: _textOrNull(json['observed_at']),
      dimensions: dimensions,
      retryAfterSeconds: json['retry_after_seconds'] is num
          ? (json['retry_after_seconds'] as num).toDouble()
          : null,
    );
  }

  final String? observedAt;
  final Map<String, QuotaDimension> dimensions;
  final double? retryAfterSeconds;
}

class ProviderTelemetry {
  const ProviderTelemetry({
    required this.name,
    required this.label,
    required this.configured,
    required this.mode,
    required this.allTime,
    required this.today,
    required this.latestQuota,
    this.portalUrl = '',
    this.rateLimitHeadersSupported = false,
  });

  factory ProviderTelemetry.fromJson(Map<String, dynamic> json) {
    final usage = _dynamicMapOrNull(json['usage']) ?? const {};
    final capabilities = _dynamicMapOrNull(json['capabilities']) ?? const {};
    final quota = _dynamicMapOrNull(json['latest_quota_snapshot']);
    return ProviderTelemetry(
      name: json['name']?.toString() ?? '',
      label: json['label']?.toString() ?? '',
      configured: json['configured'] == true,
      mode: json['mode']?.toString() ?? '',
      allTime: usage['all_time'] is Map
          ? UsagePeriod.fromJson(
              Map<String, dynamic>.from(usage['all_time'] as Map),
            )
          : const UsagePeriod(),
      today: usage['today'] is Map
          ? UsagePeriod.fromJson(
              Map<String, dynamic>.from(usage['today'] as Map),
            )
          : const UsagePeriod(),
      latestQuota: quota == null
          ? const QuotaSnapshot()
          : QuotaSnapshot.fromJson(quota),
      portalUrl: capabilities['portal_url']?.toString() ?? '',
      rateLimitHeadersSupported:
          capabilities['rate_limit_response_headers'] == true,
    );
  }

  final String name;
  final String label;
  final bool configured;
  final String mode;
  final UsagePeriod allTime;
  final UsagePeriod today;
  final QuotaSnapshot latestQuota;
  final String portalUrl;
  final bool rateLimitHeadersSupported;
}

class AdminProviderTelemetry {
  const AdminProviderTelemetry({
    required this.name,
    required this.label,
    required this.status,
    this.supported = false,
    this.configured = false,
    this.usage = const {},
    this.costUsd,
    this.creditBalanceUsd,
    this.currentInvoiceUsd,
    this.softLimitUsd,
    this.hardLimitUsd,
    this.balanceSignConvention = '',
    this.errorCodes = const [],
    this.window = const AdminProviderWindow(),
  });

  factory AdminProviderTelemetry.fromJson(Map<String, dynamic> json) {
    final usageSection = _dynamicMapOrNull(json['usage']) ?? const {};
    final usage = _dynamicMapOrNull(usageSection['usage']) ?? const {};
    final cost = _dynamicMapOrNull(json['cost']) ?? const {};
    final credit = _dynamicMapOrNull(json['credit_balance']) ?? const {};
    final billing =
        _dynamicMapOrNull(json['current_billing_period']) ?? const {};
    final limits = _dynamicMapOrNull(json['spending_limits']) ?? const {};
    final window = _dynamicMapOrNull(json['window']);
    final errors = <String>[];
    void collect(Map<String, dynamic> section) {
      final code = _textOrNull(section['error_code']);
      if (code != null && !errors.contains(code)) errors.add(code);
    }

    collect(json);
    collect(usageSection);
    collect(cost);
    collect(credit);
    collect(billing);
    collect(limits);
    return AdminProviderTelemetry(
      name: json['name']?.toString() ?? '',
      label: json['label']?.toString() ?? '',
      status: json['status']?.toString() ?? '',
      supported: json['supported'] == true,
      configured: json['configured'] == true,
      usage: usage.map((key, value) => MapEntry(key.toString(), _asInt(value))),
      costUsd:
          _textOrNull(cost['amount_usd']) ??
          _textOrNull(usageSection['amount_usd']),
      creditBalanceUsd: _textOrNull(credit['provider_reported_usd']),
      currentInvoiceUsd: _textOrNull(billing['estimated_invoice_usd']),
      softLimitUsd: _textOrNull(limits['effective_soft_usd']),
      hardLimitUsd: _textOrNull(limits['effective_hard_usd']),
      balanceSignConvention: credit['sign_convention']?.toString() ?? '',
      errorCodes: List.unmodifiable(errors),
      window: window == null
          ? const AdminProviderWindow()
          : AdminProviderWindow.fromJson(window),
    );
  }

  final String name;
  final String label;
  final String status;
  final bool supported;
  final bool configured;
  final Map<String, int> usage;
  final String? costUsd;
  final String? creditBalanceUsd;
  final String? currentInvoiceUsd;
  final String? softLimitUsd;
  final String? hardLimitUsd;
  final String balanceSignConvention;
  final List<String> errorCodes;
  final AdminProviderWindow window;
}

/// Effective query window reported by one Provider's management API adapter.
///
/// Only presentation-safe window metadata is retained. Unknown fields are
/// intentionally ignored so backend schema additions do not break the app.
class AdminProviderWindow {
  const AdminProviderWindow({
    this.startingAt,
    this.endingAt,
    this.requestedStartingAt,
    this.requestedEndingAt,
    this.alignment,
    this.bucketWidth,
    this.exactBudgetWindow,
    this.completeThrough,
  });

  factory AdminProviderWindow.fromJson(Map<String, dynamic> json) =>
      AdminProviderWindow(
        startingAt: _strictStringOrNull(json['starting_at']),
        endingAt: _strictStringOrNull(json['ending_at']),
        requestedStartingAt: _strictStringOrNull(json['requested_starting_at']),
        requestedEndingAt: _strictStringOrNull(json['requested_ending_at']),
        alignment: _strictStringOrNull(json['alignment']),
        bucketWidth: _strictStringOrNull(json['bucket_width']),
        exactBudgetWindow: json['exact_budget_window'] is bool
            ? json['exact_budget_window'] as bool
            : null,
        completeThrough: _strictStringOrNull(json['complete_through']),
      );

  final String? startingAt;
  final String? endingAt;
  final String? requestedStartingAt;
  final String? requestedEndingAt;
  final String? alignment;
  final String? bucketWidth;
  final bool? exactBudgetWindow;
  final String? completeThrough;

  bool get hasData =>
      startingAt != null ||
      endingAt != null ||
      requestedStartingAt != null ||
      requestedEndingAt != null ||
      alignment != null ||
      bucketWidth != null ||
      exactBudgetWindow != null ||
      completeThrough != null;
}

class AdminTelemetrySnapshot {
  const AdminTelemetrySnapshot({
    this.enabled = false,
    this.generatedAt = '',
    this.lookbackDays = 0,
    this.cacheHit = false,
    this.providers = const [],
    this.limitations = const [],
  });

  factory AdminTelemetrySnapshot.fromJson(Map<String, dynamic> json) {
    final window = _dynamicMapOrNull(json['window']) ?? const {};
    final cache = _dynamicMapOrNull(json['cache']) ?? const {};
    return AdminTelemetrySnapshot(
      enabled: json['enabled'] == true,
      generatedAt: json['generated_at']?.toString() ?? '',
      lookbackDays: _asInt(window['lookback_days']),
      cacheHit: cache['hit'] == true,
      providers: _mapList(json['providers'], AdminProviderTelemetry.fromJson),
      limitations: _stringList(json['limitations']),
    );
  }

  final bool enabled;
  final String generatedAt;
  final int lookbackDays;
  final bool cacheHit;
  final List<AdminProviderTelemetry> providers;
  final List<String> limitations;
}

class UsageTelemetrySnapshot {
  const UsageTelemetrySnapshot({
    required this.generatedAt,
    required this.providers,
    required this.finance,
    required this.admin,
    required this.limitations,
    this.conversationCount = 0,
    this.turnCount = 0,
  });

  factory UsageTelemetrySnapshot.fromJson(Map<String, dynamic> json) =>
      UsageTelemetrySnapshot(
        generatedAt: json['generated_at']?.toString() ?? '',
        providers: _mapList(json['providers'], ProviderTelemetry.fromJson),
        finance: json['finance'] is Map
            ? BudgetSnapshot.fromJson(
                Map<String, dynamic>.from(json['finance'] as Map),
              )
            : const BudgetSnapshot(),
        admin: json['admin'] is Map
            ? AdminTelemetrySnapshot.fromJson(
                Map<String, dynamic>.from(json['admin'] as Map),
              )
            : const AdminTelemetrySnapshot(),
        limitations: _stringList(json['limitations']),
        conversationCount: _asInt(json['conversation_count']),
        turnCount: _asInt(json['turn_count']),
      );

  final String generatedAt;
  final List<ProviderTelemetry> providers;
  final BudgetSnapshot finance;
  final AdminTelemetrySnapshot admin;
  final List<String> limitations;
  final int conversationCount;
  final int turnCount;
}

class ConversationSummary {
  const ConversationSummary({
    required this.id,
    required this.title,
    required this.updatedAt,
    required this.turnCount,
    required this.preview,
  });

  factory ConversationSummary.fromJson(Map<String, dynamic> json) =>
      ConversationSummary(
        id: json['id']?.toString() ?? '',
        title: json['title']?.toString() ?? '新しい会話',
        updatedAt: json['updated_at']?.toString() ?? '',
        turnCount: _asInt(json['turn_count']),
        preview: json['preview']?.toString() ?? '',
      );

  final String id;
  final String title;
  final String updatedAt;
  final int turnCount;
  final String preview;
}

class ConversationSearchResult {
  const ConversationSearchResult({required this.query, required this.results});

  /// 一覧系レスポンスは `{"items": [...]}` 封筒で統一されている
  /// (0.2.0で `results` から改名)。
  factory ConversationSearchResult.fromJson(Map<String, dynamic> json) =>
      ConversationSearchResult(
        query: json['query']?.toString() ?? '',
        results: _mapList(json['items'], ConversationSummary.fromJson),
      );

  final String query;
  final List<ConversationSummary> results;
}

class CancelRunResult {
  const CancelRunResult({
    required this.ok,
    required this.requestId,
    this.cancellationRequested = false,
    this.cancelled = false,
    this.alreadyDone = false,
    this.terminalOutcome = '',
    this.providerStopGuaranteed = false,
    this.warning = '',
  });

  factory CancelRunResult.fromJson(Map<String, dynamic> json) =>
      CancelRunResult(
        ok: json['ok'] == true,
        requestId: json['request_id']?.toString() ?? '',
        cancellationRequested: json['cancellation_requested'] == true,
        cancelled: json['cancelled'] == true,
        alreadyDone: json['already_done'] == true,
        terminalOutcome: json['terminal_outcome']?.toString() ?? '',
        providerStopGuaranteed: json['provider_stop_guaranteed'] == true,
        warning: json['warning']?.toString() ?? '',
      );

  final bool ok;
  final String requestId;
  final bool cancellationRequested;
  final bool cancelled;
  final bool alreadyDone;
  final String terminalOutcome;
  final bool providerStopGuaranteed;
  final String warning;
}

/// 1回のProvider呼び出しの終わり方。未知の値は [unknown] にして、
/// 「completedとみなして良い」に倒さない。
enum CompletionStatus {
  completed('completed'),
  incomplete('incomplete'),
  cancelled('cancelled'),
  failed('failed'),
  unknown('');

  const CompletionStatus(this.wireName);

  final String wireName;

  static CompletionStatus parse(Object? raw, {CompletionStatus? fallback}) =>
      switch (raw?.toString()) {
        'completed' => CompletionStatus.completed,
        'incomplete' => CompletionStatus.incomplete,
        'cancelled' => CompletionStatus.cancelled,
        'failed' => CompletionStatus.failed,
        null || '' => fallback ?? CompletionStatus.unknown,
        _ => CompletionStatus.unknown,
      };
}

/// immutable attemptの状態。
enum AttemptStatus {
  completed('completed'),
  interrupted('interrupted'),
  failed('failed'),
  unknown('');

  const AttemptStatus(this.wireName);

  final String wireName;

  static AttemptStatus parse(Object? raw) => switch (raw?.toString()) {
    'completed' => AttemptStatus.completed,
    'interrupted' => AttemptStatus.interrupted,
    'failed' => AttemptStatus.failed,
    _ => AttemptStatus.unknown,
  };
}

/// 1ターンの状態。以前は status / cancelled / interrupted / failed の4つが
/// 同じ事実を別々に持ち、書き手が片方だけ埋めると表示が壊れた。
/// 保存・表示ともこの1つを正とし、boolはここから導出する。
enum TurnStatus {
  completed('completed'),
  running('running'),
  interrupted('interrupted'),
  failed('failed'),
  unknown('');

  const TurnStatus(this.wireName);

  final String wireName;

  static TurnStatus parse(Object? raw) => switch (raw?.toString()) {
    'completed' => TurnStatus.completed,
    'running' => TurnStatus.running,
    'interrupted' => TurnStatus.interrupted,
    'failed' => TurnStatus.failed,
    _ => TurnStatus.unknown,
  };
}

class AnswerRecord {
  const AnswerRecord({
    required this.source,
    required this.ok,
    this.text = '',
    this.error = '',
    this.model = '',
    this.elapsedSec = 0,
    this.mock = false,
    this.round = 1,
    this.round1Text = '',
    this.round1Model = '',
    this.round1ElapsedSec = 0,
    this.round1CompletionStatus = CompletionStatus.unknown,
    this.round1Partial = false,
    this.round1Usage = const {},
    this.round1RequestAudit = const {},
    this.debateError = '',
    this.usage = const {},
    this.completionStatus = CompletionStatus.completed,
    this.partial = false,
    this.incompleteReason = '',
    this.usageMayBeIncomplete = false,
    this.requestAudit = const {},
    this.reasoning = const {},
    this.citations = const [],
    this.webSearchRequested = false,
  });

  factory AnswerRecord.fromJson(Map<String, dynamic> json) {
    final audit = _dynamicMapOrNull(json['request_audit']) ?? const {};
    return AnswerRecord(
      source: json['source']?.toString() ?? '',
      ok: json['ok'] == true,
      text: json['text']?.toString() ?? '',
      error: json['error']?.toString() ?? '',
      model: json['model']?.toString() ?? '',
      elapsedSec: _asDouble(json['elapsed_sec']),
      mock: json['mock'] == true,
      round: _asInt(json['round'], fallback: 1),
      round1Text: json['round1_text']?.toString() ?? '',
      round1Model: json['round1_model']?.toString() ?? '',
      round1ElapsedSec: _asDouble(json['round1_elapsed_sec']),
      round1CompletionStatus: CompletionStatus.parse(
        json['round1_completion_status'],
      ),
      round1Partial: json['round1_partial'] == true,
      round1Usage:
          (json['round1_usage'] as Map?)?.map(
            (key, value) => MapEntry(key.toString(), _asInt(value)),
          ) ??
          const {},
      round1RequestAudit:
          _dynamicMapOrNull(json['round1_request_audit']) ?? const {},
      debateError: json['debate_error']?.toString() ?? '',
      usage:
          (json['usage'] as Map?)?.map(
            (key, value) => MapEntry(key.toString(), _asInt(value)),
          ) ??
          const {},
      completionStatus: CompletionStatus.parse(
        json['completion_status'],
        fallback: CompletionStatus.completed,
      ),
      partial: json['partial'] == true,
      incompleteReason: json['incomplete_reason']?.toString() ?? '',
      usageMayBeIncomplete:
          json['usage_may_be_incomplete'] == true ||
          audit['usage_may_be_incomplete'] == true,
      requestAudit: audit,
      reasoning: _dynamicMapOrNull(json['reasoning']) ?? const {},
      citations: _mapList(json['citations'], CitationRecord.fromJson),
      webSearchRequested: json['web_search_requested'] == true,
    );
  }

  final String source;
  final bool ok;
  final String text;
  final String error;
  final String model;
  final double elapsedSec;
  final bool mock;
  final int round;
  final String round1Text;
  final String round1Model;
  final double round1ElapsedSec;
  final CompletionStatus round1CompletionStatus;
  final bool round1Partial;
  final Map<String, int> round1Usage;
  final Map<String, dynamic> round1RequestAudit;
  final String debateError;
  final Map<String, int> usage;
  final CompletionStatus completionStatus;
  final bool partial;
  final String incompleteReason;
  final bool usageMayBeIncomplete;
  final Map<String, dynamic> requestAudit;
  final Map<String, dynamic> reasoning;
  final List<CitationRecord> citations;
  final bool webSearchRequested;
}

class SynthesisRecord {
  const SynthesisRecord({
    required this.ok,
    this.text = '',
    this.error = '',
    this.source = '',
    this.model = '',
    this.elapsedSec = 0,
    this.mock = false,
    this.skipped = false,
    this.usage = const {},
    this.completionStatus = CompletionStatus.completed,
    this.partial = false,
    this.incompleteReason = '',
    this.usageMayBeIncomplete = false,
    this.requestAudit = const {},
  });

  factory SynthesisRecord.fromJson(Map<String, dynamic> json) {
    final audit = _dynamicMapOrNull(json['request_audit']) ?? const {};
    return SynthesisRecord(
      ok: json['ok'] == true,
      text: json['text']?.toString() ?? '',
      error: json['error']?.toString() ?? '',
      source: json['source']?.toString() ?? '',
      model: json['model']?.toString() ?? '',
      elapsedSec: _asDouble(json['elapsed_sec']),
      mock: json['mock'] == true,
      skipped: json['skipped'] == true,
      usage:
          (json['usage'] as Map?)?.map(
            (key, value) => MapEntry(key.toString(), _asInt(value)),
          ) ??
          const {},
      completionStatus: CompletionStatus.parse(
        json['completion_status'],
        fallback: CompletionStatus.completed,
      ),
      partial: json['partial'] == true,
      incompleteReason: json['incomplete_reason']?.toString() ?? '',
      usageMayBeIncomplete:
          json['usage_may_be_incomplete'] == true ||
          audit['usage_may_be_incomplete'] == true,
      requestAudit: audit,
    );
  }

  final bool ok;
  final String text;
  final String error;
  final String source;
  final String model;
  final double elapsedSec;
  final bool mock;
  final bool skipped;
  final Map<String, int> usage;
  final CompletionStatus completionStatus;
  final bool partial;
  final String incompleteReason;
  final bool usageMayBeIncomplete;
  final Map<String, dynamic> requestAudit;
}

class RegenerationAttempt {
  const RegenerationAttempt({
    required this.attemptId,
    required this.target,
    required this.provider,
    required this.status,
    this.parentAttemptId,
    this.createdAt = '',
    this.completedAt = '',
    this.original = false,
    this.usageMayBeIncomplete = false,
    this.result = const {},
  });

  factory RegenerationAttempt.fromJson(Map<String, dynamic> json) =>
      RegenerationAttempt(
        attemptId: json['attempt_id']?.toString() ?? '',
        target: json['target']?.toString() ?? '',
        provider: json['provider']?.toString() ?? '',
        status: AttemptStatus.parse(json['status']),
        parentAttemptId: _textOrNull(json['parent_attempt_id']),
        createdAt: json['created_at']?.toString() ?? '',
        completedAt: json['completed_at']?.toString() ?? '',
        original: json['original'] == true,
        usageMayBeIncomplete: json['usage_may_be_incomplete'] == true,
        result:
            (json['result'] as Map?)?.map(
              (key, value) => MapEntry(key.toString(), value),
            ) ??
            const {},
      );

  final String attemptId;
  final String target;
  final String provider;
  final AttemptStatus status;
  final String? parentAttemptId;
  final String createdAt;
  final String completedAt;
  final bool original;
  final bool usageMayBeIncomplete;
  final Map<String, dynamic> result;
}

class AttachmentRecord {
  const AttachmentRecord({
    required this.id,
    required this.conversationId,
    required this.name,
    required this.mimeType,
    required this.kind,
    required this.sizeBytes,
    this.createdAt = '',
    this.expiresAt = '',
    this.textExtractable = false,
    this.includedInPrompt = false,
    this.truncated = false,
  });

  factory AttachmentRecord.fromJson(Map<String, dynamic> json) =>
      AttachmentRecord(
        id: json['id']?.toString() ?? '',
        conversationId: json['conversation_id']?.toString() ?? '',
        name: json['name']?.toString() ?? 'attachment',
        mimeType: json['mime_type']?.toString() ?? '',
        kind: json['kind']?.toString() ?? '',
        sizeBytes: _asInt(json['size_bytes']),
        createdAt: json['created_at']?.toString() ?? '',
        expiresAt: json['expires_at']?.toString() ?? '',
        textExtractable: json['text_extractable'] == true,
        includedInPrompt: json['included_in_prompt'] == true,
        truncated: json['truncated'] == true,
      );

  final String id;
  final String conversationId;
  final String name;
  final String mimeType;
  final String kind;
  final int sizeBytes;
  final String createdAt;
  final String expiresAt;
  final bool textExtractable;
  final bool includedInPrompt;
  final bool truncated;
}

class TurnRecord {
  const TurnRecord({
    required this.requestId,
    required this.message,
    required this.cleanMessage,
    required this.answers,
    required this.synthesis,
    required this.options,
    this.resumeRequest = const {},
    this.insights,
    this.status = TurnStatus.completed,
    this.cancelledByUser = false,
    this.usageMayBeIncomplete = false,
    this.attempts = const [],
    this.activeAttempts = const {},
    this.synthesisStale = false,
    this.attachments = const [],
  });

  factory TurnRecord.fromJson(Map<String, dynamic> json) {
    final rawAnswers = json['answers'] as Map? ?? const {};
    final answers = <String, AnswerRecord>{};
    for (final entry in rawAnswers.entries) {
      if (entry.value is Map) {
        answers[entry.key.toString()] = AnswerRecord.fromJson(
          Map<String, dynamic>.from(entry.value as Map),
        );
      }
    }
    final rawSynthesis = json['synthesis'];
    return TurnRecord(
      requestId: json['request_id']?.toString() ?? '',
      message: json['message']?.toString() ?? '',
      cleanMessage: json['clean_message']?.toString() ?? '',
      answers: answers,
      synthesis: rawSynthesis is Map
          ? SynthesisRecord.fromJson(Map<String, dynamic>.from(rawSynthesis))
          : const SynthesisRecord(ok: false),
      options:
          (json['options'] as Map?)?.map(
            (key, value) => MapEntry(key.toString(), value),
          ) ??
          const {},
      resumeRequest:
          (json['resume_request'] as Map?)?.map(
            (key, value) => MapEntry(key.toString(), value),
          ) ??
          const {},
      insights: _dynamicMapOrNull(json['insights']),
      status: _turnStatus(json),
      cancelledByUser: json['cancelled'] == true,
      usageMayBeIncomplete: json['usage_may_be_incomplete'] == true,
      attempts: _mapList(json['attempts'], RegenerationAttempt.fromJson),
      activeAttempts:
          (json['active_attempts'] as Map?)?.map(
            (key, value) => MapEntry(key.toString(), value.toString()),
          ) ??
          const {},
      synthesisStale: json['synthesis_stale'] == true,
      attachments: _mapList(json['attachments'], AttachmentRecord.fromJson),
    );
  }

  final String requestId;
  final String message;
  final String cleanMessage;
  final Map<String, AnswerRecord> answers;
  final SynthesisRecord synthesis;
  final Map<String, dynamic> options;
  final Map<String, dynamic> resumeRequest;
  final Map<String, dynamic>? insights;

  /// このターンの状態。表示・分岐はすべてここだけを見る。
  final TurnStatus status;

  /// 「利用者が停止した」だけは通信断([TurnStatus.interrupted])と意味が違うので
  /// 独立したフラグとして残す。
  final bool cancelledByUser;

  final bool usageMayBeIncomplete;
  final List<RegenerationAttempt> attempts;
  final Map<String, String> activeAttempts;
  final bool synthesisStale;
  final List<AttachmentRecord> attachments;

  List<String> get providers => _stringList(options['providers']);

  bool get completed => status == TurnStatus.completed;
  bool get running => status == TurnStatus.running;
  bool get interrupted => status == TurnStatus.interrupted;
  bool get failed => status == TurnStatus.failed;

  /// 旧名の別名。停止は中断の一種であり、状態としては同じ扱いになる。
  bool get cancelled => cancelledByUser;
}

/// statusを正としつつ、statusを持たない旧recordだけbool群から復元する。
TurnStatus _turnStatus(Map<String, dynamic> json) {
  final parsed = TurnStatus.parse(json['status']);
  if (parsed != TurnStatus.unknown) return parsed;
  if (json['failed'] == true) return TurnStatus.failed;
  if (json['interrupted'] == true || json['cancelled'] == true) {
    return TurnStatus.interrupted;
  }
  return json.containsKey('status') ? TurnStatus.unknown : TurnStatus.completed;
}

class ConversationRecord {
  const ConversationRecord({
    required this.id,
    required this.title,
    required this.turns,
    this.memory = const ConversationMemory(),
  });

  factory ConversationRecord.fromJson(Map<String, dynamic> json) =>
      ConversationRecord(
        id: json['id']?.toString() ?? '',
        title: json['title']?.toString() ?? '新しい会話',
        turns: _mapList(json['turns'], TurnRecord.fromJson),
        memory: json['memory'] is Map
            ? ConversationMemory.fromJson(
                Map<String, dynamic>.from(json['memory'] as Map),
              )
            : const ConversationMemory(),
      );

  final String id;
  final String title;
  final List<TurnRecord> turns;
  final ConversationMemory memory;
}

class ConversationMemory {
  const ConversationMemory({
    this.revision = 0,
    this.text = '',
    this.updatedAt = '',
    this.secretCandidatesRedacted = false,
  });

  factory ConversationMemory.fromJson(Map<String, dynamic> json) =>
      ConversationMemory(
        revision: _asInt(json['revision']),
        text: json['text']?.toString() ?? '',
        updatedAt: json['updated_at']?.toString() ?? '',
        secretCandidatesRedacted: json['secret_candidates_redacted'] == true,
      );

  final int revision;
  final String text;
  final String updatedAt;
  final bool secretCandidatesRedacted;
}

class LiveTurn {
  LiveTurn({
    required this.requestId,
    required this.message,
    required List<String> providers,
    required this.tier,
    this.reasoningMode = 'auto',
    required this.debate,
    required this.synthesize,
    this.blind = false,
    this.webSearch = false,
    this.confirmedLiveApi = false,
    this.confirmedSensitiveData = false,
    this.conversationId = '',
    List<String> attachmentIds = const [],
  }) : providers = List.of(providers),
       attachmentIds = List.of(attachmentIds);

  final String requestId;
  final String message;
  final List<String> providers;
  final List<String> attachmentIds;
  final String tier;
  final String reasoningMode;
  final bool debate;
  final bool synthesize;
  final bool blind;
  final bool webSearch;
  final bool confirmedLiveApi;
  final bool confirmedSensitiveData;
  String conversationId;
  String phase = '回答を待っています';
  final Map<String, AnswerRecord> answers = {};
  SynthesisRecord? synthesis;
  Map<String, dynamic>? insights;
  String error = '';
  String lastEventId = '';
}

List<String> _stringList(dynamic value) => value is List
    ? value.map((item) => item.toString()).toList(growable: false)
    : const [];

List<T> _mapList<T>(dynamic value, T Function(Map<String, dynamic>) decode) {
  if (value is! List) return const [];
  return value
      .whereType<Map>()
      .map((item) => decode(Map<String, dynamic>.from(item)))
      .toList(growable: false);
}

Map<String, int> _intMap(dynamic value) =>
    (value as Map?)?.map(
      (key, item) => MapEntry(key.toString(), _asInt(item)),
    ) ??
    const {};

Map<String, dynamic>? _dynamicMapOrNull(dynamic value) =>
    value is Map ? Map<String, dynamic>.from(value) : null;

int _asInt(dynamic value, {int fallback = 0}) {
  if (value is int) return value;
  if (value is num) return value.toInt();
  return int.tryParse(value?.toString() ?? '') ?? fallback;
}

int? _nullableInt(dynamic value) {
  if (value == null || value is bool) return null;
  if (value is int) return value;
  if (value is num) return value.toInt();
  return int.tryParse(value.toString());
}

/// 何であれ文字列化し、空文字はnullにする。
String? _textOrNull(dynamic value) {
  if (value == null) return null;
  final text = value.toString().trim();
  return text.isEmpty ? null : text;
}

/// String以外は値があってもnullにする(数値やbooleanを表示へ流さない)。
String? _strictStringOrNull(dynamic value) {
  if (value is! String) return null;
  final text = value.trim();
  return text.isEmpty ? null : text;
}

double _asDouble(dynamic value) {
  if (value is num) return value.toDouble();
  return double.tryParse(value?.toString() ?? '') ?? 0;
}
