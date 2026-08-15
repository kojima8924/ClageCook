/// Providerの表示順・表示名カタログ。
///
/// home_screen.dartとwidgets/turn_view.dartに完全重複していた定数を一本化した。
/// 表示順・表示名を変えるときは必ずこのファイルだけを更新する。
const providerOrder = ['claude', 'gemini', 'chatgpt', 'grok'];

const providerLabels = {
  'claude': 'Claude',
  'gemini': 'Gemini',
  'chatgpt': 'ChatGPT',
  'grok': 'Grok',
};
