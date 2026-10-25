import 'package:dio/dio.dart';

import '../../../core/network/api_client.dart';

/// 대화 한 번의 결과: 보낸 말과 대답 · 남은 대화 횟수 · 글로만 대답했는지.
typedef Exchange = ({List<Map<String, dynamic>> messages, int? left, bool textOnly});

/// 페르소나 만들기·대화 API (talkto_api_server /api/v1).
class PersonaApi {
  static Map<String, dynamic>? _meta;

  /// 질문·선지 목록. 서버가 정의를 갖고 있어 화면은 그대로 그린다.
  static Future<Map<String, dynamic>> meta() async {
    return _meta ??= Map<String, dynamic>.from(await ApiClient.get('/intake/meta'));
  }

  /// [meta] 의 만들 때 질문 묶음(sections) → 묶음 머리 한 줄 안내. 안내가 없는 묶음은 빠진다.
  static Map<String, String> sectionLeads(Map<String, dynamic> meta) => {
        for (final s in meta['sections'] as List? ?? const [])
          if (s is Map && s['id'] is String && s['lead'] is String) s['id'] as String: s['lead'] as String,
      };

  /// 이 대화 상대의 질문 목록. 필수 질문은 대화 상대마다 다르다(2026-10-21 개편 뒤 만든 가족 대화 상대,
  /// 예전에 만든 것, 나 자신). 서버가 상세([detail])에 이 대화 상대 것(questions·sections·moreQuestions·
  /// moreModules)을 주면 그것으로, 주지 않은 것은 [meta] 의 것으로 그린다.
  ///
  /// 상세의 questions 는 서버(intake.questions_for)가 required·title·default 를 이 대화 상대에 맞춘 것이다.
  /// questions 를 주지 않는 서버면 meta 의 required(개편 뒤 가족 기준)가 예전 대화 상대·나 자신에게 틀리니,
  /// 상세의 requiredQuestions 와 기본 정보로 필수와 문구만 맞춘다([_fitted]).
  static Map<String, dynamic> forPersona(Map<String, dynamic> meta, Map<String, dynamic> detail) {
    final merged = <String, dynamic>{
      ...meta,
      for (final key in const ['questions', 'sections', 'moreQuestions', 'moreModules'])
        if (detail[key] != null) key: detail[key],
    };
    if (detail['questions'] == null && detail['requiredQuestions'] is List && merged['questions'] is List) {
      merged['questions'] = _fitted(merged['questions'] as List, meta, detail);
    }
    return merged;
  }

  /// questions 없는 상세용: 서버 intake.questions_for 와 같게 맞춘다.
  /// 필수: 개편 뒤 가족·고인(basics.intakeVersion 2, 나 자신 아님)은 meta 의 required 그대로(기본 정보가
  /// 대신한 P1-07A·살아 계신 분의 임종 이야기처럼 지금 묻지 않는 것도 — 화면이 기본 정보로 거르고, 정보
  /// 고치기에서 기본 정보를 바꾸면 다시 묻는다), 예전 대화 상대·나 자신은 requiredQuestions(예전 넷).
  /// 문구: intake.question_title(나 자신은 개편 전 문구 그대로 — titleSelf 가 있으면 그것, 없으면 title.
  /// 살아 계신·대화가 어려우신 분은 titleLiving 이 있으면 그것, 그 밖은 title). 보기 글: 나 자신은
  /// placeholderSelf 가 있으면 그것.
  static List<Map<String, dynamic>> _fitted(List questions, Map<String, dynamic> meta, Map<String, dynamic> detail) {
    final ids = {for (final id in detail['requiredQuestions'] as List) '$id'};
    final basics = detail['basics'] is Map ? detail['basics'] as Map : const {};
    final self = basics['relation'] == '나 자신';
    final living = basics['status'] == 'alive' || basics['status'] == 'unable';
    final legacy = {for (final id in meta['legacyRequired'] as List? ?? const []) '$id'};
    // 기본 정보에 질문 차례가 없으면(아주 예전 서버) 필수 목록이 예전 넷과 다른지로 본다
    final v2 = !self &&
        (basics['intakeVersion'] == 2 ||
            (legacy.isNotEmpty && !(ids.length == legacy.length && ids.containsAll(legacy))));
    return [
      for (final q in questions)
        if (q is Map)
          {
            ...Map<String, dynamic>.from(q),
            'required': v2 ? q['required'] == true : ids.contains(q['id']),
            'title': self
                ? q['titleSelf'] ?? q['title']
                : (living && q['titleLiving'] != null ? q['titleLiving'] : q['title']),
            if (self && q['placeholderSelf'] != null) 'placeholder': q['placeholderSelf'],
          },
    ];
  }

  static Future<List<Map<String, dynamic>>> list() async {
    final data = await ApiClient.get('/personas') as List;
    return [for (final item in data) Map<String, dynamic>.from(item)];
  }

  static Future<Map<String, dynamic>> create(Map<String, dynamic> basics) async {
    return Map<String, dynamic>.from(await ApiClient.post('/personas', basics));
  }

  static Future<Map<String, dynamic>> detail(String id) async {
    return Map<String, dynamic>.from(await ApiClient.get('/personas/$id'));
  }

  /// 대화 상대를 지운다. 돌려주는 값: {deleted, doneAt, backupUntil, vendorOk, message}
  /// (message = 서버가 정한 지운 뒤 안내 — 서버 백업에 남는 날까지 적혀 있다). 예전 서버는 빈 값.
  static Future<Map<String, dynamic>> delete(String id) async {
    final data = await ApiClient.delete('/personas/$id');
    return data is Map ? Map<String, dynamic>.from(data) : <String, dynamic>{};
  }

  /// 입력한 정보(기본 정보·질문 답)를 고친다. 다 만든 페르소나면 서버가 지침을 뒤에서 다시 만들고
  /// 돌려준 값의 updating 이 true 다. 진행은 detail 의 jobs.UPDATE 로 본다.
  static Future<Map<String, dynamic>> update(
    String id,
    Map<String, dynamic> basics, {
    Map<String, dynamic>? answers,
  }) async {
    final data = await ApiClient.put('/personas/$id', {'basics': basics, if (answers != null) 'answers': answers});
    return Map<String, dynamic>.from(data);
  }

  /// 이 화면이 다루는 문항([ids])만 내 답으로 바꾸고, 나머지 답은 서버에 있는 지금 값을 둔다.
  /// 정보 고치기 ↔ 기억 더 채우기처럼 두 화면이 각자 연 답을 저장해도 서로 지우지 않게.
  /// [basics] 를 주지 않으면 서버의 지금 기본 정보를 그대로 보낸다.
  static Future<Map<String, dynamic>> updateAnswers(
    String id, {
    required Map<String, dynamic> answers,
    required Iterable<String> ids,
    Map<String, dynamic>? basics,
  }) async {
    final fresh = await detail(id);
    final mine = ids.toSet();
    // 'M3-03:detail' 같은 덧붙은 답도 문항 id 로 묶는다
    bool own(String key) => mine.contains(key.split(':').first);
    final merged = <String, dynamic>{
      for (final e in Map<String, dynamic>.from(fresh['answers'] ?? {}).entries)
        if (!own(e.key)) e.key: e.value,
      for (final e in answers.entries)
        if (own(e.key)) e.key: e.value,
    };
    return update(id, basics ?? Map<String, dynamic>.from(fresh['basics'] ?? {}), answers: merged);
  }

  /// 녹음을 올린다. [pledge] 는 '녹음 올리기 전 확인'(recording_pledge)에 체크했다는 뜻이다.
  /// 서버는 이 대화 상대의 확인 기록이 없고 pledge 도 없으면 받지 않는다(400).
  static Future<List<dynamic>> upload(
    String id,
    List<({String name, List<int> bytes})> files, {
    bool pledge = false,
    void Function(double progress)? onProgress,
  }) async {
    final form = FormData.fromMap({if (pledge) 'pledge': 'true'});
    for (final file in files) {
      form.files.add(MapEntry('files', MultipartFile.fromBytes(file.bytes, filename: file.name)));
    }
    final data = await ApiClient.call(
      'POST',
      '/personas/$id/recordings',
      data: form,
      onSendProgress: (sent, total) {
        if (total > 0) onProgress?.call(sent / total);
      },
    );
    return data as List;
  }

  /// 다 만든 페르소나에 더 올린 녹음을 반영한다. 진행은 detail 의 jobs.EXTEND 로 본다.
  static Future<void> applyRecordings(String id) => ApiClient.post('/personas/$id/recordings/apply');

  // ── 글 자료(카카오톡·문자·메일·블로그·일기) ──

  static Future<Map<String, dynamic>> materials(String id) async =>
      Map<String, dynamic>.from(await ApiClient.get('/personas/$id/materials'));

  /// 파일 여러 개 또는 붙여넣은 글 하나를 더한다. [pledge] 는 '남긴 글 올리기 전 확인'(material_pledge)에
  /// 체크했다는 뜻이다. 서버는 확인 기록이 없고 pledge 도 없으면 받지 않는다(400).
  static Future<Map<String, dynamic>> addMaterials(
    String id, {
    required String kind,
    List<({String name, List<int> bytes})> files = const [],
    String? text,
    String? title,
    bool pledge = false,
    void Function(double progress)? onProgress,
  }) async {
    final form = FormData.fromMap({
      'kind': kind,
      if (pledge) 'pledge': 'true',
      if (text != null && text.trim().isNotEmpty) 'text': text,
      if (title != null && title.trim().isNotEmpty) 'title': title,
    });
    for (final file in files) {
      form.files.add(MapEntry('files', MultipartFile.fromBytes(file.bytes, filename: file.name)));
    }
    return Map<String, dynamic>.from(
      await ApiClient.call(
        'POST',
        '/personas/$id/materials',
        data: form,
        onSendProgress: (sent, total) {
          if (total > 0) onProgress?.call(sent / total);
        },
      ),
    );
  }

  static Future<Map<String, dynamic>> updateMaterial(
    String id,
    String materialId, {
    bool? include,
    String? title,
  }) async =>
      Map<String, dynamic>.from(
        await ApiClient.call('PATCH', '/personas/$id/materials/$materialId', data: {
          if (include != null) 'include': include,
          if (title != null) 'title': title,
        }),
      );

  static Future<Map<String, dynamic>> deleteMaterial(String id, String materialId) async =>
      Map<String, dynamic>.from(await ApiClient.delete('/personas/$id/materials/$materialId'));

  static Future<List<dynamic>> deleteRecording(String id, String recordingId) async {
    return await ApiClient.delete('/personas/$id/recordings/$recordingId') as List;
  }

  static Future<void> transcribe(String id) => ApiClient.post('/personas/$id/transcribe');

  /// 그분 목소리(VOICE_1 …)를 고른다. 한 사람이 나뉘어 묶였으면 여럿.
  static Future<void> chooseVoices(String id, List<String> voiceKeys) =>
      ApiClient.post('/personas/$id/speakers', {'voiceKeys': voiceKeys});

  static Future<void> backToSpeakers(String id) => ApiClient.post('/personas/$id/back-to-speakers');

  /// 녹음을 더 올리러 돌아간다. 이미 옮긴 녹음은 다시 옮기지 않는다.
  static Future<void> backToUpload(String id) => ApiClient.post('/personas/$id/back-to-upload');

  static Future<List<String>> phraseSuggestions(String id) async {
    final data = await ApiClient.get('/personas/$id/phrase-suggestions') as List;
    return data.cast<String>();
  }

  static Future<void> saveAnswers(String id, Map<String, dynamic> answers) =>
      ApiClient.put('/personas/$id/answers', answers);

  /// 말로 적기: 녹음(3분·6MB까지)을 글로 옮겨 받는다. 서버는 소리를 남기지 않는다. 만든 분과 함께하는
  /// 가족 모두 쓴다(질문 답·더 알려 주실 기억·대화 고쳐 주기). 못 알아들으면 422(no_speech), 너무 길거나
  /// 크면 400, 너무 자주 하면 429 — 셋 다 서버가 준 한국어 안내를 그대로 보인다.
  static Future<String> transcribeAnswer(String id, ({List<int> bytes, String filename}) clip) async {
    final form = FormData.fromMap({'audio': MultipartFile.fromBytes(clip.bytes, filename: clip.filename)});
    final data = await ApiClient.call('POST', '/personas/$id/transcriptions', data: form);
    return data is Map ? '${data['text'] ?? ''}' : '';
  }

  /// 페르소나를 만든다. 그분 목소리도 늘 함께 만든다. 목소리 사용 동의는 처음 한 번 받는다
  /// (다시 시도할 때는 서버가 남겨 둔 동의를 쓴다).
  static Future<void> build(String id, Map<String, dynamic> answers, {bool voiceConsent = false}) =>
      ApiClient.post('/personas/$id/build', {
        'answers': answers,
        'cloneVoice': true,
        if (voiceConsent) 'voiceConsent': true,
      });

  /// 다 만든 페르소나의 목소리만 다시 복제한다. 진행은 detail 의 jobs.VOICE 로 본다.
  /// 목소리 사용 동의가 없는 페르소나(예전에 목소리 없이 만든 것)는 voiceConsent 로 이때 동의를 보낸다.
  static Future<void> remakeVoice(String id, {bool voiceConsent = false}) =>
      ApiClient.post('/personas/$id/voice', {if (voiceConsent) 'voiceConsent': true});

  /// 대화 상대별 선택 동의(지금은 'sensitive_religion'·'sensitive_health')를 하거나 거둔다.
  /// 거두면 서버가 그 범주 질문의 답을 지우고, 다 만든 대화 상대면 다시 익힌다.
  /// 돌려주는 값: {consents:{voice, recordingPledge, …}, updating:bool}
  static Future<Map<String, dynamic>> setPersonaConsent(String id, String type, bool agreed) async =>
      Map<String, dynamic>.from(await ApiClient.post('/personas/$id/consents', {'type': type, 'agreed': agreed}));

  static Future<void> backToQuestions(String id) => ApiClient.post('/personas/$id/back-to-questions');

  // ── 대화 ──

  static Future<String> conversation(String id) async {
    final data = await ApiClient.post('/personas/$id/conversation');
    return data['conversationId'] as String;
  }

  static Future<List<Map<String, dynamic>>> messages(String conversationId) async {
    final data = await ApiClient.get('/conversations/$conversationId');
    return [for (final m in data['messages'] as List) Map<String, dynamic>.from(m)];
  }

  /// 보낸 말과 대답, 이번 대화를 센 뒤 남은 대화 횟수(null = 제한 없음), 글로만 대답했는지
  /// (대화 횟수를 다 쓴 뒤에는 끊지 않고 글로 대답한다).
  static Exchange _exchange(dynamic data) => (
        messages: [
          Map<String, dynamic>.from(data['userMessage']),
          Map<String, dynamic>.from(data['assistantMessage']),
        ],
        left: (data['conversationsLeft'] as num?)?.toInt(),
        textOnly: data['textOnly'] == true,
      );

  static Future<Exchange> sendText(
    String conversationId,
    String text,
  ) async {
    final data = await ApiClient.post(
      '/conversations/$conversationId/messages/text',
      {'content': text},
    );
    return _exchange(data);
  }

  /// 말로 보내기: 서버가 받아쓰고 바로 대답까지 만든다.
  static Future<Exchange> sendVoice(
    String conversationId,
    List<int> bytes,
    String filename,
  ) async {
    final form = FormData.fromMap({
      'file': MultipartFile.fromBytes(bytes, filename: filename),
    });
    final data = await ApiClient.call(
      'POST',
      '/conversations/$conversationId/messages/voice',
      data: form,
    );
    return _exchange(data);
  }

  static Future<String> speech(String messageId) async {
    final data = await ApiClient.post('/messages/$messageId/speech');
    return data['audioUrl'] as String;
  }

  /// 그분 대답 하나 평가(말풍선 ⋯). [tags]: LIKE·STYLE·CONTENT·TOO_LONG·VOICE. [correction] 은 말투·내용에
  /// 붙이는 '그분이라면 어떻게 말하셨을까요?' 한 줄(300자, 글 예시로만 쓴다). 다시 보내면 고친다.
  /// 돌려준 값의 state: applied(만든 분 고침 반영)·pending_owner(가족 고침, 만든 분 확인 전)·none·rejected.
  static Future<Map<String, dynamic>> feedback(
    String messageId,
    String rating, {
    List<String> tags = const [],
    String? correction,
  }) async {
    final data = await ApiClient.post('/messages/$messageId/feedback', {
      'rating': rating,
      'tags': tags,
      if (correction != null && correction.trim().isNotEmpty) 'correction': correction.trim(),
    });
    return data is Map ? Map<String, dynamic>.from(data) : <String, dynamic>{};
  }

  /// 대화 전체에 한 줄(대화 화면 위 '한마디', 300자). 관리자만 본다.
  static Future<void> conversationFeedback(String conversationId, String comment) =>
      ApiClient.post('/conversations/$conversationId/feedback', {'comment': comment});
}
