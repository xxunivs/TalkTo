/// 지금 페이지 주소(# 뒤는 뺌). 소셜 로그인이 끝나고 돌아올 곳.
String currentPageUrl() => '';

/// 지금 페이지 주소 전체(# 뒤 포함).
String currentFullUrl() => '';

/// 휴대폰 공유 창을 열 수 있는가(웹이 아니면 없다).
bool canShare() => false;

/// 휴대폰 공유 창을 연다. 보냈으면 true, 그만두면 null, 열지 못하면 false.
Future<bool?> shareLink({required String url, String? text}) async => false;

/// 카카오톡 앱 안의 브라우저인가.
bool isKakaoInApp() => false;

/// 메신저·SNS 앱 안의 브라우저인가.
bool isInAppBrowser() => false;

/// 휴대폰의 기본 브라우저로 연다.
void openExternal(String url) {}

/// 이 탭에서 다른 주소로 이동한다(카카오·구글 로그인 화면).
void goToUrl(String url) {}

/// 새 탭으로 연다.
void openInNewTab(String url) {}

/// 받은 파일을 내려받게 한다(웹이 아니면 하지 않는다).
void downloadBytes(String filename, List<int> bytes, String mimeType) {}

/// 파일 끌어다 놓기(웹 PC 전용). 웹이 아니면 받지 않는다.
void Function() listenFileDrop({
  required void Function(List<({String name, List<int> bytes})> files) onFiles,
  void Function(bool hovering)? onHover,
  int? Function()? maxBytes,
  void Function(List<String> names)? onTooBig,
}) =>
    () {};

/// 브라우저가 만든 임시 주소(blob:)를 푼다(웹이 아니면 하지 않는다).
void revokeBlobUrl(String url) {}

/// 올려 둔 웹 빌드 번호. 웹이 아니면 없다.
Future<String?> fetchDeployedBuild() async => null;

/// 페이지 새로 읽기(웹 전용).
void reloadPage() {}

/// 다시 돌아왔을 때(웹 전용).
void onPageVisible(void Function() callback) {}
