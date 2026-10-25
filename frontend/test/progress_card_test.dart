import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:talkto_fe/shared/widgets/ui.dart';

void main() {
  testWidgets('진행 카드: 진행률이 있으면 막대와 몇 %를 보여 주고, 없으면 막대가 없다', (tester) async {
    await tester.pumpWidget(
      const MaterialApp(home: Scaffold(body: ProgressCard(title: '녹음을 듣고 적는 중 (1/2)', percent: 40))),
    );
    await tester.pump(const Duration(seconds: 1));
    expect(find.text('40%'), findsOneWidget);
    expect(find.byType(LinearProgressIndicator), findsOneWidget);

    await tester.pumpWidget(const MaterialApp(home: Scaffold(body: ProgressCard(title: '준비하는 중'))));
    await tester.pump(const Duration(seconds: 1));
    expect(find.byType(LinearProgressIndicator), findsNothing);
  });

  testWidgets('긴 상태 꼬리표는 좁은 화면에서 넘치지 않고 줄을 바꾼다', (tester) async {
    await tester.pumpWidget(
      const MaterialApp(
        home: Scaffold(
          body: SizedBox(width: 160, child: StatusChip('완전히 닮은 목소리로 대화할 수 있어요', positive: true)),
        ),
      ),
    );
    expect(tester.takeException(), isNull);
  });
}
