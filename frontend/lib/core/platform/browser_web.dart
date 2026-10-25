import 'dart:async';
import 'dart:js_interop';
import 'dart:js_interop_unsafe';
import 'dart:typed_data';

import 'package:web/web.dart' as web;

/// 지금 페이지 주소(# 뒤는 뺌). 소셜 로그인이 끝나고 돌아올 곳.
String currentPageUrl() {
  final location = web.window.location;
  return '${location.origin}${location.pathname}';
}

/// 지금 페이지 주소 전체(# 뒤 포함). 다른 브라우저로 열 때 초대 주소(#/join/…)를 그대로 넘긴다.
String currentFullUrl() => web.window.location.href;

/// 휴대폰 공유 창(카카오톡·문자 등)을 열 수 있는 브라우저인가. 없으면 [보내기]를 숨긴다.
bool canShare() {
  try {
    return web.window.navigator.has('share');
  } catch (_) {
    return false;
  }
}

/// 휴대폰 공유 창을 연다. 단추를 누른 그 자리에서 불러야 한다(브라우저는 누른 직후에만 허락한다).
/// 보냈으면 true, 받는 사람을 고르다 그만두면 null, 공유 창이 없거나 열리지 않으면 false.
Future<bool?> shareLink({required String url, String? text}) async {
  if (!canShare()) return false;
  try {
    final data = text == null ? web.ShareData(url: url) : web.ShareData(url: url, text: text);
    await web.window.navigator.share(data).toDart;
    return true;
  } catch (error) {
    // AbortError = 그만둠. NotAllowedError(누른 때가 지남) 등은 열지 못한 것
    return error.toString().contains('AbortError') ? null : false;
  }
}

String get _userAgent => web.window.navigator.userAgent;

/// 카카오톡 앱 안의 브라우저인가.
bool isKakaoInApp() => _userAgent.contains('KAKAOTALK');

/// 메신저·SNS 앱 안의 브라우저인가(카카오톡·네이버·인스타그램·페이스북·라인).
/// 이런 곳에서는 Google 로그인이 막힌다(카카오 로그인은 된다).
bool isInAppBrowser() => RegExp(r'KAKAOTALK|NAVER\(inapp|Instagram|FBAN|FBAV|Line/').hasMatch(_userAgent);

/// 앱 안 브라우저에서 휴대폰의 기본 브라우저로 연다. 카카오톡은 전용 주소로, 그 밖에는 새 창으로.
void openExternal(String url) {
  if (isKakaoInApp()) {
    web.window.location.assign('kakaotalk://web/openExternal?url=${Uri.encodeComponent(url)}');
  } else {
    web.window.open(url, '_blank');
  }
}

/// 이 탭에서 다른 주소로 이동한다(카카오·구글 로그인 화면).
void goToUrl(String url) => web.window.location.assign(url);

/// 새 탭으로 연다(고객센터의 카카오톡 채널 등 다른 사이트). 새 탭이 이 화면(window.opener)을 건드리거나
/// 이 화면 주소를 받지 못하게 noopener·noreferrer 로 연다.
void openInNewTab(String url) => web.window.open(url, '_blank', 'noopener,noreferrer');

/// 받은 파일(채운 동의서 PDF 등)을 내려받게 한다.
void downloadBytes(String filename, List<int> bytes, String mimeType) {
  final blob = web.Blob(
    <JSAny>[Uint8List.fromList(bytes).toJS].toJS,
    web.BlobPropertyBag(type: mimeType),
  );
  final url = web.URL.createObjectURL(blob);
  final anchor = web.HTMLAnchorElement()
    ..href = url
    ..download = filename;
  web.document.body?.append(anchor);
  anchor.click();
  anchor.remove();
  // 바로 풀면 내려받기가 끊기는 브라우저가 있어 조금 뒤에 푼다
  Timer(const Duration(seconds: 30), () => web.URL.revokeObjectURL(url));
}

/// 브라우저가 만든 임시 주소(blob:, 녹음 끝난 소리 등)를 풀어 메모리를 돌려준다.
void revokeBlobUrl(String url) {
  if (!url.startsWith('blob:')) return;
  try {
    web.URL.revokeObjectURL(url);
  } catch (_) {
    // 이미 풀렸으면 그대로 둔다
  }
}

/// 화면에 파일을 끌어다 놓으면 받는다(PC). onHover(true/false)로 끌고 있는지 알려 준다.
/// maxBytes 가 주는 크기를 넘는 파일은 읽지 않고 onTooBig 으로 이름만 알린다.
/// 돌려주는 함수를 부르면 그만 받는다.
void Function() listenFileDrop({
  required void Function(List<({String name, List<int> bytes})> files) onFiles,
  void Function(bool hovering)? onHover,
  int? Function()? maxBytes,
  void Function(List<String> names)? onTooBig,
}) {
  var depth = 0;
  bool hasFiles(web.DragEvent event) {
    final types = event.dataTransfer?.types.toDart ?? const <JSString>[];
    return types.any((t) => t.toDart == 'Files');
  }

  final enter = ((web.DragEvent event) {
    if (!hasFiles(event)) return;
    event.preventDefault();
    depth++;
    onHover?.call(true);
  }).toJS;
  final over = ((web.DragEvent event) {
    if (!hasFiles(event)) return;
    event.preventDefault(); // 놓을 수 있게(안 하면 브라우저가 파일을 열어 버린다)
    event.dataTransfer?.dropEffect = 'copy';
  }).toJS;
  final leave = ((web.DragEvent event) {
    if (!hasFiles(event)) return;
    depth = depth > 0 ? depth - 1 : 0;
    if (depth == 0) onHover?.call(false);
  }).toJS;
  final drop = ((web.DragEvent event) {
    if (!hasFiles(event)) return;
    event.preventDefault();
    depth = 0;
    onHover?.call(false);
    final list = event.dataTransfer?.files;
    if (list == null || list.length == 0) return;
    final files = [for (var i = 0; i < list.length; i++) list.item(i)!];
    final limit = maxBytes?.call();
    final tooBig = [for (final file in files) if (limit != null && file.size > limit) file.name];
    () async {
      final read = <({String name, List<int> bytes})>[];
      for (final file in files) {
        if (limit != null && file.size > limit) continue;
        final buffer = await file.arrayBuffer().toDart;
        read.add((name: file.name, bytes: buffer.toDart.asUint8List()));
      }
      if (tooBig.isNotEmpty) onTooBig?.call(tooBig);
      onFiles(read);
    }();
  }).toJS;

  web.document.addEventListener('dragenter', enter);
  web.document.addEventListener('dragover', over);
  web.document.addEventListener('dragleave', leave);
  web.document.addEventListener('drop', drop);
  return () {
    web.document.removeEventListener('dragenter', enter);
    web.document.removeEventListener('dragover', over);
    web.document.removeEventListener('dragleave', leave);
    web.document.removeEventListener('drop', drop);
  };
}

/// 올려 둔 웹 빌드 번호(version.json 의 build_number). 캐시를 쓰지 않고 읽는다. 못 읽으면 null.
Future<String?> fetchDeployedBuild() async {
  try {
    final res = await web.window
        .fetch('version.json?t=${DateTime.now().millisecondsSinceEpoch}'.toJS, web.RequestInit(cache: 'no-store'))
        .toDart;
    if (!res.ok) return null;
    final data = (await res.json().toDart).dartify();
    return data is Map ? data['build_number']?.toString() : null;
  } catch (_) {
    return null;
  }
}

/// 이 페이지를 새로 읽는다(새 버전으로).
void reloadPage() => web.window.location.reload();

/// 탭이나 홈 화면 앱으로 다시 돌아왔을 때 부른다.
void onPageVisible(void Function() callback) {
  web.document.addEventListener(
    'visibilitychange',
    ((web.Event _) {
      if (web.document.visibilityState == 'visible') callback();
    }).toJS,
  );
}
