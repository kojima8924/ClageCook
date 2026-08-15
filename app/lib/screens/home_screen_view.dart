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
                      : directByok
                      ? 'DIRECT · LOCAL'
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
                  helperText: 'Ctrl/Cmd+K · 最大200文字',
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
      ],
    );
  }

  Widget _chatPane() {
    return Column(
      children: [
        if (_error.isNotEmpty) _errorBanner(_error),
        if (_streamDisconnected && _liveTurn != null)
          MaterialBanner(
            content: const Text('サーバー側で継続中の可能性があります。再接続または停止を選べます。'),
            actions: [
              TextButton(
                onPressed: _cancelLiveTurn,
                child: const Text('停止を要求'),
              ),
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
            onReconnect: turn.status == 'running'
                ? () => unawaited(_reconnectSavedTurn(turn))
                : null,
            onCancel: turn.status == 'running'
                ? () => unawaited(_cancelSavedTurn(turn))
                : null,
            regenerationPending: _regenerationActionIds.any(
              (id) => id.startsWith('${turn.requestId}:'),
            ),
            onRegenerateAnswer: turn.status == 'completed'
                ? (provider) => unawaited(
                    _regenerateSavedTurn(
                      turn,
                      target: 'answer',
                      provider: provider,
                    ),
                  )
                : null,
            onRegenerateSynthesis:
                turn.status == 'completed' && !turn.synthesis.skipped
                ? () =>
                      unawaited(_regenerateSavedTurn(turn, target: 'synthesis'))
                : null,
            onForkEdit: turn.status == 'completed'
                ? () => unawaited(_forkEditTurn(turn))
                : null,
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
            showTokenUsageLedger: _showTokenUsageLedger,
          ),
      ],
    );
  }

  Widget _emptyState() {
    final theme = Theme.of(context);
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
              if (_server == null || _server!.activeWorkers.isEmpty) ...[
                const SizedBox(height: 18),
                FilledButton.icon(
                  onPressed: _sending || _liveTurn != null
                      ? null
                      : _openSettings,
                  icon: const Icon(Icons.settings_ethernet),
                  label: const Text('実行方式を設定'),
                ),
              ],
            ],
          ),
        ),
      ),
    );
  }

  Widget _composerStrip({required Key key, required List<Widget> children}) {
    return SizedBox(
      key: key,
      height: 48,
      child: SingleChildScrollView(
        scrollDirection: Axis.horizontal,
        child: Row(mainAxisSize: MainAxisSize.min, children: children),
      ),
    );
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
                  key: const Key('composer-quality-strip'),
                  children: [
                    if (runActive) ...[
                      Chip(
                        avatar: const Icon(Icons.edit_note, size: 17),
                        label: const Text('次回'),
                        visualDensity: VisualDensity.compact,
                        side: BorderSide(color: theme.colorScheme.primary),
                      ),
                      const SizedBox(width: 6),
                    ],
                    Padding(
                      padding: const EdgeInsets.symmetric(horizontal: 4),
                      child: Text('品質', style: theme.textTheme.labelLarge),
                    ),
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
                  ],
                ),
                _composerStrip(
                  key: const Key('composer-effort-strip'),
                  children: [
                    Padding(
                      padding: const EdgeInsets.symmetric(horizontal: 4),
                      child: Text('エフォート', style: theme.textTheme.labelLarge),
                    ),
                    Tooltip(
                      message:
                          '既定は${_defaultReasoningMode.toUpperCase()}です。変更は次の会議だけに適用します。',
                      child: Semantics(
                        label: '推論エフォート',
                        child: SegmentedButton<String>(
                          key: const Key('composer-effort-selector'),
                          style: compactSegmentStyle,
                          segments: const [
                            ButtonSegment(value: 'default', label: Text('既定')),
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
                          _webSearch ? Icons.public : Icons.public_off_outlined,
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
                    const SizedBox(width: 8),
                    for (final provider in providerOrder.where(
                      active.contains,
                    )) ...[
                      FilterChip(
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

  Widget _errorBanner(String message) {
    final colors = Theme.of(context).colorScheme;
    return Material(
      color: colors.errorContainer,
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 8),
        child: Row(
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
      ),
    );
  }
}
