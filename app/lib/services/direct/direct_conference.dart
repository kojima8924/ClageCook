// 会議1回分の実行(並列回答→相互批評→統合)と、実行中stateの保持。
part of '../direct_byok_client.dart';

extension _DirectConference on DirectByokClient {
  Future<void> _executeRun(
    _DirectRunState state, {
    required LocalConversationDocument initialDocument,
    required String message,
    required String tier,
    required ReasoningMode reasoningMode,
    required List<DirectProvider> providers,
    required bool debate,
    required bool synthesize,
    required bool blind,
    required bool webSearch,
    required List<_DirectAttachment> attachments,
  }) async {
    final answers = <String, Map<String, dynamic>>{};
    Map<String, dynamic>? synthesis;
    DirectRunGuardLease? runGuardLease;
    final attachmentIds = attachments
        .map((attachment) => attachment.id)
        .toList(growable: false);
    try {
      runGuardLease = await _runGuard.acquire(
        jobId: state.requestId,
        operation: DirectRunOperation.conference,
      );
      state.startHeartbeat(_heartbeatInterval);
      state.emit('meta', {
        'request_id': state.requestId,
        'conversation_id': state.conversationId,
        'backends': providers.map((item) => item.name).toList(),
        'mode': 'direct-byok',
        'tier': tier,
        'reasoning_mode': reasoningMode.name,
        'debate': debate,
        'blind': blind,
        'web_search': webSearch,
        'synthesizer': _synthesizer?.name ?? '',
      });
      final attachmentContext = _attachmentContext(attachments);
      final modelMessage = '$message$attachmentContext';
      final prompt = _workerPrompt(initialDocument.value, modelMessage);
      final futures = <Future<void>>[];
      for (final provider in providers) {
        futures.add(
          _callProvider(
            state,
            provider: provider,
            prompt: prompt,
            system: _workerSystem,
            tier: tier,
            reasoningMode: reasoningMode,
            webSearch: webSearch,
            round: 1,
          ).then((answer) {
            if (state.cancelled) return;
            answers[provider.name] = answer;
            state.emit('answer', answer);
          }),
        );
      }
      await Future.wait(futures);
      if (state.cancelled) {
        await _persistTurn(
          state,
          message: message,
          tier: tier,
          reasoningMode: reasoningMode,
          providers: providers,
          debate: debate,
          synthesize: synthesize,
          blind: blind,
          webSearch: webSearch,
          answers: answers,
          synthesis: const {
            'ok': false,
            'skipped': true,
            'source': 'none',
            'completion_status': 'cancelled',
          },
          attachmentIds: attachmentIds,
          attachments: attachments,
          status: 'interrupted',
          cancelled: true,
        );
        state.done(failed: true, cancelled: true);
        return;
      }

      final completedProviders = providers
          .where((provider) => answers[provider.name]?['ok'] == true)
          .toList(growable: false);
      if (debate && completedProviders.length >= 2) {
        state.emit('phase', {'name': 'debate', 'status': 'started'});
        final roundOne = _cloneMapMap(answers);
        final debateFutures = <Future<void>>[];
        for (final provider in completedProviders) {
          debateFutures.add(
            _callProvider(
              state,
              provider: provider,
              prompt: _debatePrompt(provider, roundOne, blind),
              system: _debateSystem,
              tier: tier,
              reasoningMode: reasoningMode,
              round: 2,
            ).then((revised) {
              if (state.cancelled) return;
              final original = roundOne[provider.name]!;
              if (revised['ok'] == true) {
                revised.addAll({
                  'round1_text': original['text'] ?? '',
                  'round1_model': original['model'] ?? '',
                  'round1_elapsed_sec': original['elapsed_sec'] ?? 0,
                  'round1_completion_status':
                      original['completion_status'] ?? '',
                  'round1_partial': original['partial'] == true,
                  'round1_usage': original['usage'] ?? const {},
                  'round1_request_audit': original['request_audit'] ?? const {},
                  'usage': _mergeUsage(original['usage'], revised['usage']),
                });
                answers[provider.name] = revised;
              } else {
                original['round'] = 2;
                original['round1_text'] = original['text'] ?? '';
                original['debate_error'] = revised['error'] ?? '相互批評に失敗しました';
                answers[provider.name] = original;
              }
              state.emit('answer', answers[provider.name]!);
            }),
          );
        }
        await Future.wait(debateFutures);
        state.emit('phase', {'name': 'debate', 'status': 'completed'});
      }

      final successful = {
        for (final provider in providers)
          if (answers[provider.name]?['ok'] == true)
            provider.name: answers[provider.name]!,
      };
      // 語彙重なりのinsightsはreference server限定機能。Directでは常に空で、
      // 空のeventや空のキーを保存しても表示は変わらないため出さない。
      if (!synthesize || providers.length == 1) {
        synthesis = const {
          'ok': true,
          'text': '',
          'source': 'none',
          'model': 'none',
          'elapsed_sec': 0.0,
          'usage': <String, int>{},
          'completion_status': 'completed',
          'skipped': true,
          'mock': false,
        };
      } else if (successful.isEmpty) {
        synthesis = {
          'ok': false,
          'error': '完了した回答がないため統合できません',
          'source': _synthesizer?.name ?? '',
          'completion_status': 'failed',
          'skipped': false,
        };
      } else {
        final synthesizer = _synthesizer;
        if (synthesizer == null) {
          synthesis = const {
            'ok': false,
            'error': '統合に使えるAPIキーがありません',
            'source': '',
            'completion_status': 'failed',
            'skipped': false,
          };
        } else {
          state.emit('phase', {'name': 'synthesis', 'status': 'started'});
          synthesis = await _callProvider(
            state,
            provider: synthesizer,
            prompt: _synthesisPrompt(modelMessage, successful, blind),
            system: _synthesisSystem,
            tier: tier,
            reasoningMode: reasoningMode,
            round: 1,
          );
          synthesis.remove('round');
          synthesis['skipped'] = false;
        }
      }
      if (!state.cancelled) state.emit('synthesis', synthesis);
      await _persistTurn(
        state,
        message: message,
        tier: tier,
        reasoningMode: reasoningMode,
        providers: providers,
        debate: debate,
        synthesize: synthesize,
        blind: blind,
        webSearch: webSearch,
        answers: answers,
        synthesis: synthesis,
        attachmentIds: attachmentIds,
        attachments: attachments,
        status: state.cancelled ? 'interrupted' : 'completed',
        cancelled: state.cancelled,
      );
      state.done(failed: state.cancelled, cancelled: state.cancelled);
    } catch (error) {
      if (!state.cancelled) {
        state.emit('error', {'message': _safeRunError(error)});
      }
      try {
        await _persistTurn(
          state,
          message: message,
          tier: tier,
          reasoningMode: reasoningMode,
          providers: providers,
          debate: debate,
          synthesize: synthesize,
          blind: blind,
          webSearch: webSearch,
          answers: answers,
          synthesis: synthesis ?? const {'ok': false, 'skipped': true},
          attachmentIds: attachmentIds,
          attachments: attachments,
          status: state.cancelled ? 'interrupted' : 'failed',
          cancelled: state.cancelled,
        );
      } catch (_) {
        // 元の安全な失敗通知を、保存失敗で覆い隠さない。
      }
      state.done(failed: true, cancelled: state.cancelled);
    } finally {
      state.closeProviderClients();
      await runGuardLease?.release();
      await state.closeStream();
      _runs.remove(state.requestId);
    }
  }

  Future<Map<String, dynamic>> _callProvider(
    _DirectRunState state, {
    required DirectProvider provider,
    required String prompt,
    required String system,
    required String tier,
    required ReasoningMode reasoningMode,
    required int round,
    bool webSearch = false,
  }) async {
    if (state.cancelled) return _failureAnswer(provider, '会議を停止しました', round);
    final client = _providerClientFactory();
    state.providerClients.add(client);
    try {
      return await client.complete(
        DirectProviderRequest(
          provider: provider,
          apiKey: _settings.apiKeyFor(provider),
          model: DirectProviderClient.modelFor(
            provider,
            tier,
            override: _settings.modelOverrideFor(provider),
          ),
          prompt: prompt,
          system: system,
          reasoningMode: reasoningMode,
          maxOutputTokens: DirectProviderClient.maxOutputTokensFor(
            provider,
            tier,
          ),
          webSearch: webSearch,
          tier: tier,
        ),
        round: round,
      );
    } on DirectProviderException catch (error) {
      return _failureAnswer(
        provider,
        error.message,
        round,
        errorCode: error.code,
        errorStage: error.stage,
        usageMayBeIncomplete: error.usageMayBeIncomplete,
        requestAudit: error.requestAudit,
      );
    } catch (error) {
      return _failureAnswer(provider, _safeRunError(error), round);
    } finally {
      state.providerClients.remove(client);
      client.close();
    }
  }
}

class _DirectRunState {
  _DirectRunState({
    required this.requestId,
    required this.conversationId,
    required this.controller,
  });

  final String requestId;
  final String conversationId;
  final StreamController<SseEvent> controller;
  final Set<DirectProviderClient> providerClients = {};
  var cancelled = false;
  var _eventId = 0;
  var _terminalSent = false;
  Timer? _heartbeatTimer;

  void startHeartbeat(Duration interval) {
    _heartbeatTimer?.cancel();
    _heartbeatTimer = Timer.periodic(interval, (_) {
      if (controller.isClosed || _terminalSent) return;
      controller.add(
        const SseEvent(event: SseDecoder.keepAliveEvent, data: {}),
      );
    });
  }

  void emit(String event, Map<String, dynamic> data) {
    if (controller.isClosed || _terminalSent) return;
    controller.add(
      SseEvent(
        event: event,
        data: Map<String, dynamic>.from(data),
        id: '${++_eventId}',
      ),
    );
  }

  void done({required bool failed, required bool cancelled}) {
    if (_terminalSent || controller.isClosed) return;
    _heartbeatTimer?.cancel();
    _heartbeatTimer = null;
    emit('done', {
      'failed': failed,
      'cancelled': cancelled,
      'conversation': {'id': conversationId},
    });
    _terminalSent = true;
  }

  void cancel() {
    cancelled = true;
    closeProviderClients();
  }

  void closeProviderClients() {
    for (final client in providerClients.toList()) {
      client.close();
    }
    providerClients.clear();
  }

  Future<void> closeStream() async {
    _heartbeatTimer?.cancel();
    _heartbeatTimer = null;
    if (!controller.isClosed) await controller.close();
  }
}
