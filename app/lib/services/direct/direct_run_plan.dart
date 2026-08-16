// 会議前に提示する実行計画(課金対象call数・出力上限・入力byte)の組み立て。
// 純粋な計算に寄せてあり、Provider呼び出しやストレージには触れない。
part of '../direct_byok_client.dart';

_DirectInputSnapshot _inputSnapshot({
  required Map<String, dynamic> conversation,
  required String message,
  required String attachmentContext,
}) {
  final modelMessage = '$message$attachmentContext';
  final prompt = _workerPrompt(conversation, modelMessage);
  final memory = conversation['memory'];
  final memoryText = memory is Map
      ? redactPolicySecrets(memory['text']?.toString() ?? '')
      : '';
  final messageBytes = utf8.encode(message).length;
  final attachmentBytes = utf8.encode(attachmentContext).length;
  final promptBytes = utf8.encode(prompt).length;
  final currentBytes = messageBytes + attachmentBytes;
  return _DirectInputSnapshot(
    messageBytes: messageBytes,
    attachmentBytes: attachmentBytes,
    memoryContentBytes: utf8.encode(memoryText).length,
    historyBytes: max(0, promptBytes - currentBytes),
    promptBytes: promptBytes,
    systemBytes: utf8.encode(_workerSystem).length,
  );
}

extension _DirectRunPlanning on DirectByokClient {
  Map<String, dynamic> _planJson({
    required String message,
    required String tier,
    required String reasoningMode,
    required bool debate,
    required List<DirectProvider> providers,
    required bool synthesize,
    required bool blind,
    required bool webSearch,
    required _DirectInputSnapshot input,
  }) {
    final resolvedTier = DirectProviderClient.normalizeTier(tier);
    final policy = scanPolicyText(message);
    final synth = _synthesizer;
    final synthEnabled = synthesize && providers.length > 1 && synth != null;
    final answerCalls = providers.length;
    final debateCalls = debate && providers.length >= 2 ? providers.length : 0;
    final synthesisCalls = synthEnabled ? 1 : 0;
    var answerTokens = 0;
    var debateTokens = 0;
    for (final provider in providers) {
      final cap = DirectProviderClient.maxOutputTokensFor(
        provider,
        resolvedTier,
      );
      answerTokens += cap;
      if (debateCalls > 0) debateTokens += cap;
    }
    final synthesisTokens = synthEnabled
        ? DirectProviderClient.maxOutputTokensFor(synth, resolvedTier)
        : 0;
    final totalTokens = answerTokens + debateTokens + synthesisTokens;
    final allowed =
        input.messageBytes > 0 &&
        providers.isNotEmpty &&
        policy['action'] != 'block' &&
        totalTokens <= _maxDirectRunOutputTokens &&
        input.workerPerCallBytes <= _maxDirectWorkerInputBytes;
    final requestedReasoning = _parseReasoning(reasoningMode);
    Map<String, dynamic> participant(DirectProvider provider, int calls) {
      final model = DirectProviderClient.modelFor(
        provider,
        resolvedTier,
        override: _settings.modelOverrideFor(provider),
      );
      return {
        'name': provider.name,
        'label': DirectProviderClient.labels[provider],
        'mode': 'live',
        'model': model,
        'billable': true,
        'max_calls': calls,
        'enabled': true,
        'max_output_tokens': DirectProviderClient.maxOutputTokensFor(
          provider,
          resolvedTier,
        ),
        'reasoning': DirectProviderClient.reasoningAuditFor(
          provider,
          model,
          requestedReasoning,
        ),
      };
    }

    return {
      'allowed': allowed,
      'block_reasons': [
        if (input.messageBytes == 0) 'empty_message',
        if (providers.isEmpty) 'no_provider',
        if (policy['action'] == 'block') 'policy_blocked',
        if (totalTokens > _maxDirectRunOutputTokens)
          'output_token_limit_exceeded',
        if (input.workerPerCallBytes > _maxDirectWorkerInputBytes)
          'input_byte_limit_exceeded',
      ],
      'billable': true,
      'mode': 'direct-byok',
      'options': {
        'tier': resolvedTier,
        'reasoning_mode': requestedReasoning.name,
        'debate_effective': debate && providers.length >= 2,
        'synthesize_effective': synthEnabled,
        'blind': blind,
        'web_search_requested': webSearch,
        'web_search_effective': webSearch,
      },
      'providers': providers
          .map((provider) => participant(provider, debateCalls > 0 ? 2 : 1))
          .toList(),
      'synthesizer': synthEnabled
          ? participant(synth, 1)
          : {
              'name': 'synthesizer',
              'label': 'Synthesizer',
              'mode': 'disabled',
              'model': '',
              'billable': false,
              'max_calls': 0,
              'enabled': false,
            },
      'calls': {
        'answers': answerCalls,
        'debate': debateCalls,
        'synthesis': synthesisCalls,
        'total': answerCalls + debateCalls + synthesisCalls,
      },
      'max_output_tokens': {
        'max_per_call': [
          for (final provider in providers)
            DirectProviderClient.maxOutputTokensFor(provider, resolvedTier),
          if (synthEnabled)
            DirectProviderClient.maxOutputTokensFor(synth, resolvedTier),
        ].fold<int>(0, max),
        'answers': answerTokens,
        'debate': debateTokens,
        'synthesis': synthesisTokens,
        'total': totalTokens,
        'live_total': totalTokens,
      },
      'retry_envelope': {
        'configured_retries_per_live_call': 0,
        'live_initial_calls': answerCalls + debateCalls + synthesisCalls,
        'additional_http_attempts': 0,
        'total_provider_executions': answerCalls + debateCalls + synthesisCalls,
        'max_output_tokens': totalTokens,
        'disclaimer': '自動再試行は行いません。',
      },
      'input_envelope': {
        'unit': 'utf8_bytes',
        'message': input.messageBytes,
        'attachment_snapshot': input.attachmentBytes,
        'memory_content': input.memoryContentBytes,
        'history': input.historyBytes,
        'worker_system_per_call': input.systemBytes,
        'worker_prompt_per_call': input.promptBytes,
        'answer_per_call': input.workerPerCallBytes,
        'answers_total': input.workerPerCallBytes * answerCalls,
        'debate_total': 0,
        'synthesis': 0,
        'total': input.workerPerCallBytes * answerCalls,
        'live_initial_total': input.workerPerCallBytes * answerCalls,
        'live_with_retries': input.workerPerCallBytes * answerCalls,
        'total_with_retries': input.workerPerCallBytes * answerCalls,
        'token_count_estimated': false,
        'future_generated_input_unknown': debateCalls > 0 || synthesisCalls > 0,
        'per_call_limit': _maxDirectWorkerInputBytes,
        'disclaimer': debateCalls > 0 || synthesisCalls > 0
            ? '初回回答の既知入力だけをUTF-8 byteで正確に表示します。相互批評・統合の入力は生成結果に依存するため未加算です。'
            : '全初回入力をUTF-8 byteで正確に表示します。',
      },
      'policy': policy,
      'warnings': [
        {
          'code': 'billable_live_api',
          'message': '端末から各社の実APIを直接呼び出します。各社で課金される可能性があります。',
        },
        if (!synthEnabled && synthesize && providers.length > 1)
          {'code': 'synthesizer_unavailable', 'message': '統合に使えるAPIキーがありません。'},
      ],
      'cost_estimate': {'available': false, 'complete': false},
      'budget': const {},
    };
  }

  Map<String, dynamic> _singleCallPlan({
    required bool allowed,
    required DirectProvider? provider,
    required String tier,
    required Map<String, dynamic> policy,
    bool attachmentSnapshotMissing = false,
  }) {
    final cap = provider == null
        ? 0
        : DirectProviderClient.maxOutputTokensFor(provider, tier);
    final participant = provider == null
        ? const <String, dynamic>{}
        : {
            'name': provider.name,
            'label': DirectProviderClient.labels[provider],
            'mode': 'live',
            'model': DirectProviderClient.modelFor(
              provider,
              tier,
              override: _settings.modelOverrideFor(provider),
            ),
            'billable': true,
            'max_calls': 1,
            'enabled': true,
          };
    return {
      'allowed':
          allowed && policy['action'] != 'block' && !attachmentSnapshotMissing,
      'block_reasons': [
        if (!allowed) 'provider_unavailable',
        if (policy['action'] == 'block') 'policy_blocked',
        if (attachmentSnapshotMissing) 'attachment_snapshot_missing',
      ],
      'billable': true,
      'mode': 'direct-byok',
      'providers': provider == null ? const [] : [participant],
      'synthesizer': {
        'name': 'synthesizer',
        'label': 'Synthesizer',
        'mode': 'disabled',
        'model': '',
        'billable': false,
        'max_calls': 0,
        'enabled': false,
      },
      'calls': {'answers': 1, 'debate': 0, 'synthesis': 0, 'total': 1},
      'max_output_tokens': {
        'max_per_call': cap,
        'answers': cap,
        'debate': 0,
        'synthesis': 0,
        'total': cap,
        'live_total': cap,
      },
      'retry_envelope': {
        'configured_retries_per_live_call': 0,
        'live_initial_calls': 1,
        'additional_http_attempts': 0,
        'total_provider_executions': 1,
        'max_output_tokens': cap,
      },
      'input_envelope': const {},
      'policy': policy,
      'warnings': [
        {'code': 'billable_live_api', 'message': '再生成は新しい実API呼び出しです。'},
        if (attachmentSnapshotMissing)
          {
            'code': 'attachment_snapshot_missing',
            'message':
                'このターンの添付本文が端末メモリに残っていないため再生成できません。'
                'Direct BYOKでは添付本文を端末内に保存せず、アプリを再起動すると失われます。'
                '同じファイルを添付し直して新しい質問として送ってください。',
          },
      ],
    };
  }

  List<DirectProvider> _resolveProviders(List<String>? requested) {
    final names = requested?.toSet();
    return _directProviderOrder
        .where(
          (provider) =>
              _settings.hasKey(provider) &&
              (names == null || names.isEmpty || names.contains(provider.name)),
        )
        .toList(growable: false);
  }
}

class _DirectInputSnapshot {
  const _DirectInputSnapshot({
    required this.messageBytes,
    required this.attachmentBytes,
    required this.memoryContentBytes,
    required this.historyBytes,
    required this.promptBytes,
    required this.systemBytes,
  });

  final int messageBytes;
  final int attachmentBytes;
  final int memoryContentBytes;
  final int historyBytes;
  final int promptBytes;
  final int systemBytes;

  int get workerPerCallBytes => promptBytes + systemBytes;
}
