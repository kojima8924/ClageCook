// home_screen.dartのUI builder群をpartファイルへ機械的に移動したもの。
// 同一ライブラリのためprivateアクセス・Key・文言・ツリー構造は本体と完全に同じ。
// ロジック(送信・再接続・検索・課金確認フロー)はhome_screen.dart本体に残している。
part of 'home_screen.dart';

/// _HomeScreenStateの画面構築部。
extension _HomeScreenView on _HomeScreenState {
  PreferredSizeWidget _appBar(bool wide, bool compact) {
    final safeMock = _server != null && !_server!.liveApiEnabled;
    final directByok = _server?.mode == 'direct-byok';
    final settingsLocked =
        _sending || _uploadingAttachment || _liveTurn != null;
    return AppBar(
      titleSpacing: wide ? 20 : null,
      title: (safeMock || directByok) && compact
          ? Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              mainAxisSize: MainAxisSize.min,
              children: [
                const Text('Clage Cook'),
                Row(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    Icon(
                      directByok ? Icons.phone_android : Icons.lock_outline,
                      size: 12,
                    ),
                    const SizedBox(width: 3),
                    Text(
                      directByok ? 'DIRECT · 端末内保存' : 'SAFE MOCK',
                      style: const TextStyle(fontSize: 10),
                    ),
                  ],
                ),
              ],
            )
          : const Text('Clage Cook'),
      actions: [
        if (_server != null && !compact)
          Padding(
            padding: const EdgeInsets.symmetric(vertical: 10),
            child: Tooltip(
              message: safeMock
                  ? '外部API呼び出しはサーバー側で施錠されています'
                  : directByok
                  ? '端末から各社APIへ直接接続し、会話を端末内へ保存します'
                  : '開発用サーバー経由の実行です',
              child: Chip(
                avatar: Icon(
                  safeMock
                      ? Icons.lock_outline
                      : directByok
                      ? Icons.phone_android
                      : _server!.mode == 'mock'
                      ? Icons.science_outlined
                      : Icons.cloud_done_outlined,
                  size: 17,
                ),
                label: Text(
                  safeMock
                      ? 'SAFE MOCK'
                      // 同じ状態を指す語を1つに揃える(狭幅表示と同じ文言)。
                      : directByok
                      ? 'DIRECT · 端末内保存'
                      : _server!.mode.toUpperCase(),
                ),
                visualDensity: VisualDensity.compact,
              ),
            ),
          ),
        IconButton(
          tooltip: '利用状況と予算',
          onPressed: _openUsage,
          icon: const Icon(Icons.monitor_heart_outlined),
        ),
        IconButton(
          tooltip: '更新',
          onPressed: _loading || _uploadingAttachment ? null : _refresh,
          icon: const Icon(Icons.refresh),
        ),
        IconButton(
          tooltip: '接続とBYOK設定',
          onPressed: settingsLocked ? null : _openSettings,
          icon: const Icon(Icons.settings_outlined),
        ),
        const SizedBox(width: 4),
      ],
    );
  }

  Widget _sidebar(bool inDrawer) {
    final query = _searchController.text.trim();
    final summaries = query.isEmpty
        ? _summaries
        : _searchResults ?? const <ConversationSummary>[];
    // キーボードショートカットの案内はキーボードのある環境だけに出す。
    const desktopPlatforms = {
      TargetPlatform.windows,
      TargetPlatform.macOS,
      TargetPlatform.linux,
    };
    final showShortcutHint = desktopPlatforms.contains(
      Theme.of(context).platform,
    );
    return Column(
      children: [
        Padding(
          padding: const EdgeInsets.all(12),
          child: Column(
            children: [
              SizedBox(
                width: double.infinity,
                child: FilledButton.icon(
                  onPressed: _uploadingAttachment
                      ? null
                      : () {
                          if (inDrawer) Navigator.of(context).pop();
                          _newConversation();
                        },
                  icon: const Icon(Icons.add_comment_outlined),
                  label: const Text('新しい会話'),
                ),
              ),
              const SizedBox(height: 10),
              TextField(
                controller: _searchController,
                focusNode: _searchFocusNode,
                textInputAction: TextInputAction.search,
                maxLength: 200,
                maxLengthEnforcement: MaxLengthEnforcement.enforced,
                decoration: InputDecoration(
                  hintText: 'すべての回答を全文検索',
                  helperText: showShortcutHint
                      ? 'Ctrl/Cmd+K · 最大200文字'
                      : '最大200文字',
                  prefixIcon: const Icon(Icons.search),
                  suffixIcon: query.isEmpty
                      ? null
                      : IconButton(
                          tooltip: 'クリア',
                          onPressed: _searchController.clear,
                          icon: const Icon(Icons.close),
                        ),
                  border: const OutlineInputBorder(),
                  isDense: true,
                ),
              ),
              if (_searching)
                const Padding(
                  padding: EdgeInsets.only(top: 8),
                  child: LinearProgressIndicator(minHeight: 2),
                ),
              if (_searchError.isNotEmpty)
                Padding(
                  padding: const EdgeInsets.only(top: 8),
                  child: Row(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Icon(
                        Icons.warning_amber_rounded,
                        size: 18,
                        color: Theme.of(context).colorScheme.error,
                      ),
                      const SizedBox(width: 6),
                      Expanded(
                        child: Text(
                          _searchError,
                          style: Theme.of(context).textTheme.bodySmall,
                        ),
                      ),
                      IconButton(
                        tooltip: '再検索',
                        visualDensity: VisualDensity.compact,
                        onPressed: () => _scheduleSearch(immediate: true),
                        icon: const Icon(Icons.refresh, size: 18),
                      ),
                    ],
                  ),
                ),
            ],
          ),
        ),
        const Divider(height: 1),
        Expanded(
          child: summaries.isEmpty
              ? Center(
                  child: Padding(
                    padding: const EdgeInsets.all(24),
                    child: Semantics(
                      liveRegion: true,
                      child: Text(
                        query.isEmpty
                            ? 'まだ会話がありません'
                            : _searching
                            ? '会話を検索しています…'
                            : '回答本文を含め、一致する会話がありません',
                        textAlign: TextAlign.center,
                      ),
                    ),
                  ),
                )
              : ListView.builder(
                  itemCount: summaries.length,
                  itemBuilder: (context, index) {
                    final item = summaries[index];
                    return ListTile(
                      selected: item.id == _selectedId,
                      leading: const Icon(Icons.forum_outlined),
                      title: Text(
                        item.title,
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                      ),
                      subtitle: Text(
                        '${item.turnCount}ターン · ${formatDate(item.updatedAt)}\n${item.preview}',
                        maxLines: 2,
                        overflow: TextOverflow.ellipsis,
                      ),
                      isThreeLine: true,
                      onTap: _uploadingAttachment
                          ? null
                          : () {
                              if (inDrawer) Navigator.of(context).pop();
                              unawaited(_selectConversation(item.id));
                            },
                      trailing: PopupMenuButton<String>(
                        tooltip: '会話メニュー',
                        enabled: !_uploadingAttachment,
                        onSelected: (action) {
                          if (inDrawer) Navigator.of(context).pop();
                          if (action == 'rename') {
                            unawaited(_renameConversation(item));
                          } else if (action == 'export') {
                            unawaited(
                              _copyConversationJson(item.id, item.title),
                            );
                          } else if (action == 'archive') {
                            unawaited(
                              _saveConversationArchive(item.id, item.title),
                            );
                          } else if (action == 'delete') {
                            unawaited(_deleteConversation(item));
                          }
                        },
                        itemBuilder: (_) => [
                          const PopupMenuItem(
                            value: 'rename',
                            child: ListTile(
                              contentPadding: EdgeInsets.zero,
                              leading: Icon(Icons.edit_outlined),
                              title: Text('タイトル変更'),
                            ),
                          ),
                          PopupMenuItem(
                            value: 'export',
                            enabled: !_exportingConversationIds.contains(
                              item.id,
                            ),
                            child: const ListTile(
                              contentPadding: EdgeInsets.zero,
                              leading: Icon(Icons.content_copy_outlined),
                              title: Text('JSONをコピー'),
                            ),
                          ),
                          PopupMenuItem(
                            value: 'archive',
                            enabled: !_exportingConversationIds.contains(
                              item.id,
                            ),
                            child: const ListTile(
                              contentPadding: EdgeInsets.zero,
                              leading: Icon(Icons.archive_outlined),
                              title: Text('ZIPを保存'),
                            ),
                          ),
                          const PopupMenuItem(
                            value: 'delete',
                            child: ListTile(
                              contentPadding: EdgeInsets.zero,
                              leading: Icon(Icons.delete_outline),
                              title: Text('削除'),
                            ),
                          ),
                        ],
                      ),
                    );
                  },
                ),
        ),
        // ドロワーからも設定・利用状況へ行けるようにする(AppBarのアイコンだけだと
        // 会話一覧を開いた状態から戻る操作が必要になる)。
        if (inDrawer) ...[
          const Divider(height: 1),
          SafeArea(
            top: false,
            child: Column(
              mainAxisSize: MainAxisSize.min,
              children: [
                ListTile(
                  leading: const Icon(Icons.settings_outlined),
                  title: const Text('設定（APIキー・実行方式）'),
                  onTap: () {
                    Navigator.of(context).pop();
                    unawaited(_openSettings());
                  },
                ),
                ListTile(
                  leading: const Icon(Icons.monitor_heart_outlined),
                  title: const Text('利用状況と予算'),
                  onTap: () {
                    Navigator.of(context).pop();
                    unawaited(_openUsage());
                  },
                ),
              ],
            ),
          ),
        ],
      ],
    );
  }

  Widget _chatPane() {
    return Column(
      children: [
        if (_error.isNotEmpty) _errorBanner(_error),
        if (_storageDefects.isNotEmpty && !_storageDefectsDismissed)
          StorageDefectBanner(
            defects: _storageDefects,
            repairable: _client?.supportsLocalStorageRepair ?? false,
            busy: _repairingStorage,
            onQuarantine: () => unawaited(_quarantineDefectiveConversations()),
            onRebuild: () => unawaited(_rebuildConversationIndex()),
            onDismiss: () => _rebuild(() => _storageDefectsDismissed = true),
          ),
        if (_streamDisconnected && _liveTurn != null)
          MaterialBanner(
            content: Text(
              (_client?.supportsRunReconnect ?? false)
                  ? 'サーバー側で継続中の可能性があります。再接続または停止を選べます。'
                  : '接続が切れました。この実行方式では後から再接続できません。停止を要求できます。',
            ),
            actions: [
              TextButton(
                onPressed: _cancelLiveTurn,
                child: const Text('停止を要求'),
              ),
              // 再接続できない実行方式では、押しても必ず失敗する選択肢を出さない。
              if (_client?.supportsRunReconnect ?? false)
                TextButton(
                  onPressed: _sending ? null : _resumeLiveTurn,
                  child: const Text('再接続'),
                ),
            ],
          ),
        if (_terminalReloadConversationId != null)
          MaterialBanner(
            content: const Text('会議は終了しました。保存済み結果だけを再読み込みしてください。'),
            actions: [
              TextButton(
                onPressed: _loadingConversation
                    ? null
                    : _reloadTerminalConversation,
                child: const Text('保存結果を再読込'),
              ),
            ],
          ),
        Expanded(child: _turnList()),
        _composer(),
      ],
    );
  }

  Widget _turnList() {
    if (_loadingConversation) {
      return const Center(child: CircularProgressIndicator());
    }
    final live = _liveTurn;
    final turns = (_conversation?.turns ?? const <TurnRecord>[])
        .where((turn) => turn.requestId != live?.requestId)
        .toList(growable: false);
    final showLive =
        live != null &&
        (_selectedId == live.conversationId || live.conversationId.isEmpty);
    if (turns.isEmpty && !showLive) return _emptyState();
    return ListView(
      controller: _scrollController,
      padding: const EdgeInsets.fromLTRB(16, 18, 16, 32),
      children: [
        if (_conversation != null)
          Padding(
            padding: const EdgeInsets.only(bottom: 16),
            child: Row(
              children: [
                Expanded(
                  child: Text(
                    _conversation!.title,
                    style: Theme.of(context).textTheme.headlineSmall,
                  ),
                ),
                const SizedBox(width: 8),
                IconButton.outlined(
                  tooltip: '会話ZIPを保存',
                  onPressed:
                      _exportingConversationIds.contains(_conversation!.id)
                      ? null
                      : () => unawaited(
                          _saveConversationArchive(
                            _conversation!.id,
                            _conversation!.title,
                          ),
                        ),
                  icon: const Icon(Icons.archive_outlined),
                ),
                const SizedBox(width: 8),
                IconButton.outlined(
                  tooltip: _conversation!.memory.text.trim().isEmpty
                      ? 'ローカルメモを追加'
                      : 'ローカルメモを編集',
                  onPressed: _sending ? null : _editConversationMemory,
                  icon: Icon(
                    _conversation!.memory.text.trim().isEmpty
                        ? Icons.note_add_outlined
                        : Icons.sticky_note_2,
                  ),
                ),
                const SizedBox(width: 8),
                IconButton.outlined(
                  tooltip: '会話JSONをコピー',
                  onPressed:
                      _exportingConversationIds.contains(_conversation!.id)
                      ? null
                      : () => unawaited(
                          _copyConversationJson(
                            _conversation!.id,
                            _conversation!.title,
                          ),
                        ),
                  icon: _exportingConversationIds.contains(_conversation!.id)
                      ? const SizedBox.square(
                          dimension: 18,
                          child: CircularProgressIndicator(strokeWidth: 2),
                        )
                      : const Icon(Icons.content_copy_outlined),
                ),
              ],
            ),
          ),
        for (final turn in turns) ...[
          SavedTurnView(
            key: ValueKey(turn.requestId),
            turn: turn,
            actionPending: _savedRunActionIds.contains(turn.requestId),
            // 再接続導線は保存turnの文字列ではなく、実行方式の能力で出し分ける。
            onReconnect:
                turn.running && (_client?.supportsRunReconnect ?? false)
                ? () => unawaited(_reconnectSavedTurn(turn))
                : null,
            onCancel: turn.running
                ? () => unawaited(_cancelSavedTurn(turn))
                : null,
            regenerationPending: _regenerationActionIds.any(
              (id) => id.startsWith('${turn.requestId}:'),
            ),
            onRegenerateAnswer: turn.completed
                ? (provider) => unawaited(
                    _regenerateSavedTurn(
                      turn,
                      target: 'answer',
                      provider: provider,
                    ),
                  )
                : null,
            onRegenerateSynthesis: turn.completed && !turn.synthesis.skipped
                ? () =>
                      unawaited(_regenerateSavedTurn(turn, target: 'synthesis'))
                : null,
            onForkEdit: turn.completed
                ? () => unawaited(_forkEditTurn(turn))
                : null,
            onOpenSettings: _openSettings,
            showTokenUsageLedger: _showTokenUsageLedger,
          ),
          const SizedBox(height: 24),
          const Divider(),
          const SizedBox(height: 20),
        ],
        if (showLive)
          LiveTurnView(
            key: ValueKey(live.requestId),
            turn: live,
            onOpenSettings: _openSettings,
            showTokenUsageLedger: _showTokenUsageLedger,
          ),
      ],
    );
  }

  Widget _emptyState() {
    final theme = Theme.of(context);
    final needsSetup = _server == null || _server!.activeWorkers.isEmpty;
    return Center(
      child: SingleChildScrollView(
        padding: const EdgeInsets.all(28),
        child: ConstrainedBox(
          constraints: const BoxConstraints(maxWidth: 620),
          child: Column(
            children: [
              Icon(
                Icons.groups_2_outlined,
                size: 76,
                color: theme.colorScheme.primary,
              ),
              const SizedBox(height: 18),
              Text('4つのAIを、1つの会議へ。', style: theme.textTheme.headlineSmall),
              const SizedBox(height: 10),
              const Text(
                'Claude・Gemini・ChatGPT・Grokが並列で回答し、'
                '最後に1つの回答へ統合します。Direct BYOKではAPIキーと端末内保存だけで動きます。',
                textAlign: TextAlign.center,
              ),
              // 初回は「何をすればいいか」を手順で示す。キー登録前は送信できない。
              if (needsSetup) ...[
                const SizedBox(height: 22),
                Card(
                  margin: EdgeInsets.zero,
                  child: Padding(
                    padding: const EdgeInsets.fromLTRB(16, 14, 16, 14),
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text('はじめの3ステップ', style: theme.textTheme.titleSmall),
                        const SizedBox(height: 10),
                        for (final step in const [
                          '1. 各社のAPIキーを登録する（1社からで動きます）',
                          '2. 入力欄の上「参加AI」から会議に出すAIを選ぶ',
                          '3. 質問を入力して送信ボタンを押す',
                        ])
                          Padding(
                            padding: const EdgeInsets.only(bottom: 4),
                            child: Text(step),
                          ),
                      ],
                    ),
                  ),
                ),
                const SizedBox(height: 16),
                FilledButton.icon(
                  onPressed: _sending || _liveTurn != null
                      ? null
                      : _openSettings,
                  icon: const Icon(Icons.key_outlined),
                  label: const Text('APIキーを登録する'),
                ),
              ],
            ],
          ),
        ),
      ),
    );
  }

  /// composerの操作帯。見出しと末尾ボタンは固定し、中身だけ横スクロールさせる。
  /// (見出しごとスクロールすると、右へ送るほど何の行か分からなくなるため)
  Widget _composerStrip({
    required Key key,
    required String label,
    required List<Widget> children,
    Widget? trailing,
  }) {
    final theme = Theme.of(context);
    return SizedBox(
      key: key,
      height: 48,
      child: Row(
        children: [
          Padding(
            padding: const EdgeInsets.only(left: 4, right: 8),
            child: Text(label, style: theme.textTheme.labelLarge),
          ),
          Expanded(
            child: SingleChildScrollView(
              scrollDirection: Axis.horizontal,
              child: Row(mainAxisSize: MainAxisSize.min, children: children),
            ),
          ),
          ?trailing,
        ],
      ),
    );
  }

  /// 折りたたみ中でも現在の設定が分かるよう、詳細ボタンのtooltipへ要約を出す。
  String _composerOptionsSummary() {
    final effort = _reasoningModeOverride == null
        ? '既定(${_defaultReasoningMode.toUpperCase()})'
        : _reasoningModeOverride!.toUpperCase();
    return [
      '品質 ${_tier.toUpperCase()}',
      'エフォート $effort',
      if (_debate) 'DEBATE',
      if (_webSearch) 'WEB',
      _synthesize ? '統合ON' : '統合OFF',
      if (_blind) 'BLIND',
    ].join(' · ');
  }

  Widget _composer() {
    final theme = Theme.of(context);
    final active = _server?.activeWorkers ?? const <String>[];
    final webSearchAvailable = _server?.webSearch.enabled ?? false;
    const compactSegmentStyle = ButtonStyle(
      minimumSize: WidgetStatePropertyAll(Size(0, 40)),
      padding: WidgetStatePropertyAll(EdgeInsets.symmetric(horizontal: 6)),
    );
    final runActive = _liveTurn != null;
    final preparing = _sending && !runActive;
    final draftLocked = _loadingConversation || preparing;
    final optionsLocked =
        _loadingConversation || _uploadingAttachment || preparing;
    final attachmentLocked =
        _loadingConversation || _uploadingAttachment || _sending || runActive;
    return Material(
      key: const Key('composer'),
      elevation: 8,
      color: theme.colorScheme.surfaceContainer,
      child: SafeArea(
        top: false,
        child: Padding(
          padding: const EdgeInsets.fromLTRB(8, 4, 8, 8),
          child: ConstrainedBox(
            constraints: const BoxConstraints(maxWidth: 1000),
            child: Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                _composerStrip(
                  key: const Key('composer-provider-strip'),
                  label: '参加AI',
                  trailing: Tooltip(
                    message: '会議の詳細設定（${_composerOptionsSummary()}）',
                    child: TextButton.icon(
                      key: const Key('composer-options-toggle'),
                      onPressed: () => _rebuild(
                        () => _showComposerOptions = !_showComposerOptions,
                      ),
                      icon: Icon(
                        _showComposerOptions
                            ? Icons.expand_less
                            : Icons.expand_more,
                        size: 18,
                      ),
                      label: const Text('詳細'),
                    ),
                  ),
                  children: [
                    if (runActive) ...[
                      Tooltip(
                        message: '生成中の会議とは別の、次回用の下書きです。',
                        child: Chip(
                          avatar: const Icon(Icons.edit_note, size: 17),
                          label: const Text('次回'),
                          visualDensity: VisualDensity.compact,
                          side: BorderSide(color: theme.colorScheme.primary),
                        ),
                      ),
                      const SizedBox(width: 6),
                    ],
                    if (active.isEmpty)
                      ActionChip(
                        key: const Key('composer-setup-providers'),
                        avatar: const Icon(Icons.key_off_outlined, size: 17),
                        label: const Text('APIキー未設定 — 設定する'),
                        onPressed: optionsLocked ? null : _openSettings,
                        visualDensity: VisualDensity.compact,
                      )
                    else
                      for (final provider in providerOrder.where(
                        active.contains,
                      )) ...[
                        FilterChip(
                          key: Key('composer-provider-$provider'),
                          label: Text(providerLabels[provider] ?? provider),
                          selected: _selectedProviders.contains(provider),
                          onSelected: optionsLocked
                              ? null
                              : (value) => _rebuild(() {
                                  if (value) {
                                    _selectedProviders.add(provider);
                                  } else {
                                    _selectedProviders.remove(provider);
                                  }
                                }),
                          visualDensity: VisualDensity.compact,
                        ),
                        const SizedBox(width: 6),
                      ],
                  ],
                ),
                if (_showComposerOptions) ...[
                  _composerStrip(
                    key: const Key('composer-quality-strip'),
                    label: '品質',
                    children: [
                      Semantics(
                        label: 'モデルと出力枠の品質',
                        child: SegmentedButton<String>(
                          key: const Key('composer-tier-selector'),
                          style: compactSegmentStyle,
                          segments: const [
                            ButtonSegment(value: 'low', label: Text('LOW')),
                            ButtonSegment(
                              value: 'balanced',
                              label: Text('BALANCED'),
                            ),
                            ButtonSegment(value: 'high', label: Text('HIGH')),
                          ],
                          selected: {_tier},
                          onSelectionChanged: optionsLocked
                              ? null
                              : (value) => _rebuild(() => _tier = value.first),
                          showSelectedIcon: false,
                        ),
                      ),
                      const SizedBox(width: 8),
                      Tooltip(
                        message: '相互批評を追加します。利用量と待ち時間が増えます。',
                        child: FilterChip(
                          label: const Text('DEBATE'),
                          avatar: const Icon(Icons.forum_outlined, size: 17),
                          selected: _debate,
                          onSelected: optionsLocked
                              ? null
                              : (value) => _rebuild(() => _debate = value),
                          visualDensity: VisualDensity.compact,
                        ),
                      ),
                      const SizedBox(width: 6),
                      Tooltip(
                        message: '各回答を最後に1つへ統合します。',
                        child: FilterChip(
                          label: const Text('統合'),
                          avatar: const Icon(Icons.auto_awesome, size: 17),
                          selected: _synthesize,
                          onSelected: optionsLocked
                              ? null
                              : (value) => _rebuild(() => _synthesize = value),
                          visualDensity: VisualDensity.compact,
                        ),
                      ),
                      const SizedBox(width: 6),
                    ],
                  ),
                  _composerStrip(
                    key: const Key('composer-effort-strip'),
                    label: 'エフォート',
                    children: [
                      Tooltip(
                        message:
                            '既定は${_defaultReasoningMode.toUpperCase()}です。変更は次の会議だけに適用します。',
                        child: Semantics(
                          label: '推論エフォート',
                          child: SegmentedButton<String>(
                            key: const Key('composer-effort-selector'),
                            style: compactSegmentStyle,
                            segments: const [
                              ButtonSegment(
                                value: 'default',
                                label: Text('既定'),
                              ),
                              ButtonSegment(value: 'low', label: Text('LOW')),
                              ButtonSegment(
                                value: 'medium',
                                label: Text('MEDIUM'),
                              ),
                              ButtonSegment(value: 'high', label: Text('HIGH')),
                            ],
                            selected: {_effortSelection},
                            onSelectionChanged: optionsLocked
                                ? null
                                : (value) => _rebuild(() {
                                    _reasoningModeOverride =
                                        value.first == 'default'
                                        ? null
                                        : value.first;
                                  }),
                            showSelectedIcon: false,
                          ),
                        ),
                      ),
                      const SizedBox(width: 8),
                      Tooltip(
                        message: webSearchAvailable
                            ? '初回回答だけWeb検索を許可します。検索利用量が増える場合があります。'
                            : '現在の実行環境はWeb検索に対応していません。',
                        child: FilterChip(
                          key: const Key('composer-web-toggle'),
                          label: Text(_webSearch ? 'WEB ON' : 'WEB OFF'),
                          avatar: Icon(
                            _webSearch
                                ? Icons.public
                                : Icons.public_off_outlined,
                            size: 17,
                          ),
                          selected: _webSearch,
                          onSelected: optionsLocked || !webSearchAvailable
                              ? null
                              : (value) => _rebuild(() => _webSearch = value),
                          visualDensity: VisualDensity.compact,
                        ),
                      ),
                      const SizedBox(width: 6),
                      Tooltip(
                        message: '批評と統合へAI名を渡さず、ブランド先入観を減らします。',
                        child: FilterChip(
                          label: const Text('BLIND'),
                          avatar: const Icon(
                            Icons.visibility_off_outlined,
                            size: 17,
                          ),
                          selected: _blind,
                          onSelected: optionsLocked
                              ? null
                              : (value) => _rebuild(() => _blind = value),
                          visualDensity: VisualDensity.compact,
                        ),
                      ),
                      const SizedBox(width: 6),
                    ],
                  ),
                ],
                if (_pendingAttachments.isNotEmpty) ...[
                  const SizedBox(height: 4),
                  Wrap(
                    spacing: 6,
                    runSpacing: 6,
                    children: [
                      for (final attachment in _pendingAttachments)
                        InputChip(
                          avatar: Icon(
                            attachment.kind == 'image'
                                ? Icons.image_outlined
                                : attachment.kind == 'pdf'
                                ? Icons.picture_as_pdf_outlined
                                : Icons.description_outlined,
                            size: 17,
                          ),
                          label: Text(
                            '${attachment.name} · ${formatBytes(attachment.sizeBytes)}',
                          ),
                          onDeleted: attachmentLocked
                              ? null
                              : () => unawaited(
                                  _removePendingAttachment(attachment),
                                ),
                        ),
                    ],
                  ),
                ],
                const SizedBox(height: 4),
                Row(
                  crossAxisAlignment: CrossAxisAlignment.end,
                  children: [
                    PopupMenuButton<String>(
                      tooltip: '質問テンプレートを挿入',
                      enabled: !draftLocked,
                      icon: const Icon(Icons.auto_fix_high_outlined),
                      onSelected: (label) =>
                          _insertPromptTemplate(_promptTemplates[label]!),
                      itemBuilder: (context) => [
                        for (final entry in _promptTemplates.entries)
                          PopupMenuItem(
                            value: entry.key,
                            child: ListTile(
                              contentPadding: EdgeInsets.zero,
                              leading: const Icon(Icons.add_comment_outlined),
                              title: Text(entry.key),
                              subtitle: Text(
                                entry.value.trim(),
                                maxLines: 2,
                                overflow: TextOverflow.ellipsis,
                              ),
                            ),
                          ),
                      ],
                    ),
                    IconButton(
                      tooltip: '添付を追加',
                      onPressed: attachmentLocked ? null : _pickAttachments,
                      icon: _uploadingAttachment
                          ? const SizedBox.square(
                              dimension: 18,
                              child: CircularProgressIndicator(strokeWidth: 2),
                            )
                          : const Icon(Icons.attach_file),
                    ),
                    const SizedBox(width: 4),
                    Expanded(
                      child: TextField(
                        controller: _messageController,
                        focusNode: _messageFocusNode,
                        minLines: 1,
                        maxLines: 6,
                        enabled: !draftLocked,
                        textCapitalization: TextCapitalization.sentences,
                        decoration: InputDecoration(
                          hintText: '質問を入力…',
                          prefixIcon: runActive
                              ? const Tooltip(
                                  message: '生成中の会議とは別の、次回用の下書きです。',
                                  child: Icon(Icons.edit_note),
                                )
                              : null,
                          border: OutlineInputBorder(),
                          isDense: true,
                        ),
                      ),
                    ),
                    const SizedBox(width: 8),
                    IconButton.filled(
                      tooltip: runActive
                          ? (_cancelPending ? '会議を停止中' : '会議を停止')
                          : '会議を開始',
                      onPressed: runActive
                          ? (_cancelPending ? null : _cancelLiveTurn)
                          : (draftLocked || _uploadingAttachment
                                ? null
                                : _send),
                      icon: _cancelPending
                          ? const SizedBox.square(
                              dimension: 18,
                              child: CircularProgressIndicator(strokeWidth: 2),
                            )
                          : runActive
                          ? const Icon(Icons.stop)
                          : _sending
                          ? const Icon(Icons.hourglass_top)
                          : const Icon(Icons.send),
                    ),
                  ],
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }

  /// APIキー・認証・接続が原因のエラーは、設定画面への復帰導線を添える。
  /// (原因表示だけだと初見が行き止まりになるため)
  bool _errorNeedsSettings(String message) =>
      const ['APIキー', '認証', '401', '実行方式', '接続'].any(message.contains);

  Widget _errorBanner(String message) {
    final colors = Theme.of(context).colorScheme;
    final needsSettings = _errorNeedsSettings(message);
    return Material(
      color: colors.errorContainer,
      child: Padding(
        padding: EdgeInsets.fromLTRB(14, 8, 4, needsSettings ? 4 : 8),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Icon(Icons.error_outline, color: colors.onErrorContainer),
                const SizedBox(width: 8),
                Expanded(
                  child: Text(
                    message,
                    style: TextStyle(color: colors.onErrorContainer),
                  ),
                ),
                IconButton(
                  tooltip: '閉じる',
                  onPressed: () => _rebuild(() => _error = ''),
                  icon: Icon(Icons.close, color: colors.onErrorContainer),
                ),
              ],
            ),
            if (needsSettings)
              TextButton.icon(
                key: const Key('error-open-settings'),
                onPressed: _openSettings,
                icon: const Icon(Icons.settings_outlined, size: 18),
                label: const Text('設定を開いてAPIキーを確認'),
                style: TextButton.styleFrom(
                  foregroundColor: colors.onErrorContainer,
                ),
              ),
          ],
        ),
      ),
    );
  }
}
