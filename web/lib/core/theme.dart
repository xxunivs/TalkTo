import 'package:flutter/material.dart';

/// 차분한 종이색 바탕에 깊은 초록. 랜딩페이지의 초록을 낮춰서, 금융 앱처럼 밝고 차갑지 않게.
/// 가족을 기억하는 서비스라 편지·사진첩 같은 온도를 목표로 한다.
class AppColors {
  static const paper = Color(0xFFF4F1EA);
  static const card = Color(0xFFFCFBF7);
  static const sunk = Color(0xFFECE8DF);
  static const ink = Color(0xFF22251F);
  static const ink2 = Color(0xFF50544B);
  static const muted = Color(0xFF878B80);
  static const line = Color(0xFFE3DED3);
  static const line2 = Color(0xFFCFC9BC);

  /// 주 색: 깊은 숲 초록. 누를 수 있는 것과 고른 것.
  static const primary = Color(0xFF2E5A48);
  static const primaryDeep = Color(0xFF223F33);
  static const primarySoft = Color(0xFFE2EAE4);

  /// 따뜻한 흙색. 추천·안내 같은 작은 강조.
  static const clay = Color(0xFF8F6240);
  static const claySoft = Color(0xFFF1E7DA);

  /// 로그인 화면의 어두운 판.
  static const night = Color(0xFF1D2823);
  static const nightInk = Color(0xFFEDE7DA);

  static const danger = Color(0xFFA8432F);
  static const dangerSoft = Color(0xFFF6E6E1);

  static const kakao = Color(0xFFFEE500);

  // 예전 이름(관리자 화면 등)
  static const accent = primary;
  static const accentSoft = primarySoft;
  static const sage = primary;
  static const sageSoft = primarySoft;
}

/// 한글을 그릴 앱 글꼴. 버튼·머리글처럼 글자 모양을 따로 정한 곳에도 꼭 붙인다.
/// 빠지면 브라우저가 인터넷에서 글꼴 조각을 받아 그리는데, 못 받은 글자는 네모(⊠)로 나온다.
const koreanFallback = ['NotoSansKR'];

/// 제목은 명조(고운바탕)로 편지 같은 느낌, 본문은 고딕.
class AppText {
  static const serif = 'GowunBatang';
  static const _fallback = koreanFallback;

  static const display = TextStyle(
    fontFamily: serif,
    fontFamilyFallback: _fallback,
    fontSize: 28,
    height: 1.38,
    fontWeight: FontWeight.w700,
    color: AppColors.ink,
    letterSpacing: -0.4,
  );
  static const title = TextStyle(
    fontFamily: serif,
    fontFamilyFallback: _fallback,
    fontSize: 22,
    height: 1.4,
    fontWeight: FontWeight.w700,
    color: AppColors.ink,
    letterSpacing: -0.3,
  );
  static const heading = TextStyle(
      fontSize: 17, height: 1.4, fontWeight: FontWeight.w700, color: AppColors.ink, fontFamilyFallback: _fallback);
  static const body = TextStyle(fontSize: 15, height: 1.6, color: AppColors.ink, fontFamilyFallback: _fallback);
  static const lead = TextStyle(fontSize: 15, height: 1.65, color: AppColors.ink2, fontFamilyFallback: _fallback);
  static const small = TextStyle(fontSize: 13, height: 1.5, color: AppColors.muted, fontFamilyFallback: _fallback);
  static const label = TextStyle(
      fontSize: 14, height: 1.4, fontWeight: FontWeight.w700, color: AppColors.ink, fontFamilyFallback: _fallback);
}

/// 화면 폭 기준. 넓으면 왼쪽에 목록을 두는 PC 배치, 좁으면 한 화면씩 넘기는 모바일 배치.
class Breakpoints {
  static const wide = 900.0;

  static bool isWide(BuildContext context) => MediaQuery.sizeOf(context).width >= wide;
}

ThemeData buildTheme() {
  final base = ThemeData(
    useMaterial3: true,
    // 영문은 기본 글꼴 그대로, 한글만 앱에 넣은 글꼴로(입력 중 네모 방지)
    fontFamilyFallback: const ['NotoSansKR'],
    colorScheme: ColorScheme.fromSeed(
      seedColor: AppColors.primary,
      primary: AppColors.primary,
      onPrimary: Colors.white,
      secondary: AppColors.clay,
      surface: AppColors.card,
      onSurface: AppColors.ink,
      error: AppColors.danger,
    ),
    scaffoldBackgroundColor: AppColors.paper,
    splashFactory: InkSparkle.splashFactory,
  );
  final rounded12 = BorderRadius.circular(12);
  return base.copyWith(
    textTheme: base.textTheme.apply(bodyColor: AppColors.ink, displayColor: AppColors.ink),
    dividerTheme: const DividerThemeData(color: AppColors.line, thickness: 1, space: 1),
    appBarTheme: const AppBarTheme(
      backgroundColor: AppColors.paper,
      foregroundColor: AppColors.ink,
      elevation: 0,
      scrolledUnderElevation: 0,
      centerTitle: false,
      titleTextStyle: TextStyle(fontSize: 17, fontWeight: FontWeight.w700, color: AppColors.ink, fontFamilyFallback: koreanFallback),
    ),
    inputDecorationTheme: InputDecorationTheme(
      filled: true,
      fillColor: Colors.white,
      contentPadding: const EdgeInsets.symmetric(horizontal: 16, vertical: 15),
      border: OutlineInputBorder(borderRadius: rounded12, borderSide: const BorderSide(color: AppColors.line2)),
      enabledBorder: OutlineInputBorder(borderRadius: rounded12, borderSide: const BorderSide(color: AppColors.line2)),
      focusedBorder: OutlineInputBorder(
        borderRadius: rounded12,
        borderSide: const BorderSide(color: AppColors.primary, width: 1.6),
      ),
      hintStyle: const TextStyle(color: AppColors.muted, fontFamilyFallback: koreanFallback),
    ),
    filledButtonTheme: FilledButtonThemeData(
      style: FilledButton.styleFrom(
        backgroundColor: AppColors.primary,
        foregroundColor: Colors.white,
        disabledBackgroundColor: AppColors.line,
        disabledForegroundColor: AppColors.muted,
        minimumSize: const Size(0, 52),
        padding: const EdgeInsets.symmetric(horizontal: 24),
        shape: const StadiumBorder(),
        textStyle: const TextStyle(fontWeight: FontWeight.w700, fontSize: 16, fontFamilyFallback: koreanFallback),
      ),
    ),
    outlinedButtonTheme: OutlinedButtonThemeData(
      style: OutlinedButton.styleFrom(
        foregroundColor: AppColors.ink,
        backgroundColor: AppColors.card,
        minimumSize: const Size(0, 52),
        padding: const EdgeInsets.symmetric(horizontal: 22),
        side: const BorderSide(color: AppColors.line2),
        shape: const StadiumBorder(),
        textStyle: const TextStyle(fontWeight: FontWeight.w700, fontSize: 16, fontFamilyFallback: koreanFallback),
      ),
    ),
    textButtonTheme: TextButtonThemeData(
      style: TextButton.styleFrom(
        foregroundColor: AppColors.ink2,
        textStyle: const TextStyle(fontWeight: FontWeight.w600, fontSize: 15, fontFamilyFallback: koreanFallback),
      ),
    ),
    checkboxTheme: CheckboxThemeData(
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(5)),
      side: const BorderSide(color: AppColors.line2, width: 1.5),
    ),
    switchTheme: SwitchThemeData(
      thumbColor: WidgetStateProperty.resolveWith(
        (s) => s.contains(WidgetState.selected) ? Colors.white : AppColors.line2,
      ),
      trackColor: WidgetStateProperty.resolveWith(
        (s) => s.contains(WidgetState.selected) ? AppColors.primary : AppColors.sunk,
      ),
      trackOutlineColor: WidgetStateProperty.all(Colors.transparent),
    ),
    progressIndicatorTheme: const ProgressIndicatorThemeData(
      color: AppColors.primary,
      linearTrackColor: AppColors.line,
    ),
    popupMenuTheme: PopupMenuThemeData(
      color: AppColors.card,
      surfaceTintColor: Colors.transparent,
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(14),
        side: const BorderSide(color: AppColors.line),
      ),
      textStyle: const TextStyle(fontSize: 15, color: AppColors.ink, fontFamilyFallback: koreanFallback),
    ),
    dialogTheme: DialogThemeData(
      backgroundColor: AppColors.card,
      surfaceTintColor: Colors.transparent,
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(22)),
      titleTextStyle: AppText.title.copyWith(fontSize: 20),
    ),
    bottomSheetTheme: const BottomSheetThemeData(
      backgroundColor: AppColors.card,
      surfaceTintColor: Colors.transparent,
      showDragHandle: true,
      dragHandleColor: AppColors.line2,
    ),
    tooltipTheme: TooltipThemeData(
      decoration: BoxDecoration(color: AppColors.ink, borderRadius: BorderRadius.circular(8)),
      textStyle: const TextStyle(color: Colors.white, fontSize: 12, fontFamilyFallback: koreanFallback),
    ),
    snackBarTheme: SnackBarThemeData(
      behavior: SnackBarBehavior.floating,
      backgroundColor: AppColors.ink,
      contentTextStyle: const TextStyle(color: Colors.white, fontSize: 14.5, fontFamilyFallback: koreanFallback),
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
    ),
  );
}
