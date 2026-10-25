/// 한국어 화면 글 도우미.
library;

/// 마지막 글자에 받침이 있는가(한글이 아니면 받침 없음으로 본다).
bool hasBatchim(String word) {
  final trimmed = word.trim();
  if (trimmed.isEmpty) return false;
  final code = trimmed.runes.last;
  if (code < 0xAC00 || code > 0xD7A3) return false;
  return (code - 0xAC00) % 28 != 0;
}

/// 받침에 맞는 조사를 붙인다. josa('할머니', '은', '는') → '할머니는', josa('선생님', '은', '는') → '선생님은'.
String josa(String word, String withBatchim, String withoutBatchim) =>
    '$word${hasBatchim(word) ? withBatchim : withoutBatchim}';

/// 페르소나를 화면에서 부르는 말. 내가 그분을 부르는 호칭(할머니) → 관계 → '그분' 순.
/// 성함은 화면에 보이지 않는다(내 할머니를 이름으로 부르는 건 예의가 아니다). 성함은 녹음에서
/// 그분을 알아보고 대화를 만드는 데만 쓴다.
String personaTitle(Map<String, dynamic> persona) {
  for (final key in ['addressTerm', 'relation']) {
    final value = (persona[key] as String?)?.trim() ?? '';
    if (value.isNotEmpty) return value;
  }
  return '그분';
}
