import 'package:flutter/foundation.dart';

import '../../../core/session.dart';
import 'persona_api.dart';

/// 내 페르소나 목록. PC 의 왼쪽 목록과 모바일 첫 화면이 같이 본다.
/// 만들기 단계가 바뀌거나 대화를 보내면 [load] 로 다시 읽는다.
class PersonaStore extends ChangeNotifier {
  List<Map<String, dynamic>>? items;
  Object? error;
  Future<void>? _loading;

  PersonaStore() {
    // 로그아웃하면 다른 사람 목록이 남지 않게 비운다
    session.addListener(() {
      if (!session.loggedIn && items != null) {
        items = null;
        error = null;
        notifyListeners();
      }
    });
  }

  Future<void> load() => _loading ??= _fetch().whenComplete(() => _loading = null);

  Future<void> _fetch() async {
    try {
      items = await PersonaApi.list();
      error = null;
    } catch (e) {
      error = e;
    }
    notifyListeners();
  }

  Map<String, dynamic>? byId(String id) {
    for (final p in items ?? const <Map<String, dynamic>>[]) {
      if (p['id'] == id) return p;
    }
    return null;
  }
}

final personaStore = PersonaStore();

/// 페르소나 정보를 고쳤다는 알림(페르소나 id, 몇 번째). 열려 있는 페르소나 화면이 다시 읽는다.
final personaEdited = ValueNotifier<(String, int)>(('', 0));

void notifyPersonaEdited(String id) => personaEdited.value = (id, personaEdited.value.$2 + 1);

/// 만들기 단계 → 사람이 읽는 상태.
const stepLabel = {
  'UPLOAD': '녹음 올리는 중',
  'SPEAKERS': '목소리 고르는 중',
  'QUESTIONS': '질문 답하는 중',
  'BUILDING': '만드는 중',
  'READY': '대화할 수 있어요',
};

/// 서버 시각을 이 기기 시각으로. 로컬 SQLite 는 시간대 없이 UTC 를 주므로 그때는 UTC 로 읽는다.
DateTime? parseServerTime(String? iso) {
  if (iso == null || iso.isEmpty) return null;
  final hasZone = RegExp(r'(Z|[+-]\d\d:?\d\d)$').hasMatch(iso);
  return DateTime.tryParse(hasZone ? iso : '${iso}Z')?.toLocal();
}

/// "오후 3:12", "어제", "10월 3일" 처럼 짧게.
String shortTime(String? iso) {
  final t = parseServerTime(iso);
  if (t == null) return '';
  final now = DateTime.now();
  final today = DateTime(now.year, now.month, now.day);
  final day = DateTime(t.year, t.month, t.day);
  final diff = today.difference(day).inDays;
  if (diff == 0) {
    final h = t.hour % 12 == 0 ? 12 : t.hour % 12;
    return '${t.hour < 12 ? '오전' : '오후'} $h:${t.minute.toString().padLeft(2, '0')}';
  }
  if (diff == 1) return '어제';
  if (t.year == now.year) return '${t.month}월 ${t.day}일';
  return '${t.year}. ${t.month}. ${t.day}.';
}
