import 'package:flutter/material.dart';

/// 글 자료를 꺼내는 방법(2026-10 기준, 각 회사 도움말로 확인). 메뉴 이름은 앱 버전마다 조금 다를 수 있다.
class MaterialGuide {
  final String kind;
  final String title;
  final IconData icon;
  final String summary;
  final List<GuideSection> sections;
  final List<String> notes;

  const MaterialGuide({
    required this.kind,
    required this.title,
    required this.icon,
    required this.summary,
    required this.sections,
    this.notes = const [],
  });
}

class GuideSection {
  final String title;
  final List<String> steps;
  final String? tip;

  const GuideSection(this.title, this.steps, {this.tip});
}

const materialGuides = <MaterialGuide>[
  MaterialGuide(
    kind: 'kakaotalk',
    title: '카카오톡 대화',
    icon: Icons.chat_bubble_outline_rounded,
    summary: "그분과 나눈 채팅방에서 '대화 내용 내보내기'로 글 파일(.txt)을 받아 올려요. 사진·영상은 빼고 글만 받으면 돼요.",
    sections: [
      GuideSection('안드로이드 (갤럭시)', [
        '그분과 나눈 채팅방을 열어요.',
        '오른쪽 위 메뉴(≡) → 설정(⚙️)을 눌러요.',
        "'대화 내용 내보내기'를 눌러요. 최신 버전은 '채팅방 데이터' 안에 있어요.",
        "'텍스트만 보내기'를 골라요.",
        '공유 창이 뜨면 메일이나 \'나와의 채팅\'으로 보내 파일을 받고, 그 .txt 파일을 여기에 올려요.',
      ]),
      GuideSection('아이폰', [
        '채팅방을 열고 오른쪽 위 메뉴(≡) → 설정(⚙️)을 눌러요.',
        "'대화 내용 내보내기'를 눌러요.",
        "'텍스트 메시지만 보내기'를 골라요.",
        "공유 창에서 '파일에 저장'을 고르거나 메일로 보내요.",
        '받은 .txt 파일을 여기에 올려요.',
      ]),
      GuideSection('컴퓨터 (윈도)', [
        '컴퓨터용 카카오톡에서 채팅방을 열어요.',
        "오른쪽 위 메뉴(≡) → '대화 내용' → '대화 내보내기'를 눌러요. (Ctrl + S)",
        '저장할 곳을 고르면 .txt 파일로 저장돼요.',
        '그 파일을 올리거나 이 화면에 끌어다 놓아요.',
      ], tip: '컴퓨터에 받아 둔 대화만 들어가요. 오래된 대화는 휴대폰에서 내보내는 편이 좋아요.'),
      GuideSection('맥', [
        '채팅방 → 메뉴(≡) → 채팅방 설정을 눌러요.',
        "'대화 내용 관리' → '대화 내용 저장'을 눌러요.",
        '저장된 .txt 파일을 올려요.',
      ]),
    ],
    notes: [
      '비밀채팅은 내보낼 수 없어요. 오픈채팅은 같은 방법(채팅방 ≡ → ⚙ → 대화 내용 내보내기)으로 내보낼 수 있어요.',
      "'모든 메시지 저장'(사진·영상 포함)은 크고 압축 파일이라 '텍스트만'을 권해요. 압축 파일을 받았다면 풀어서 안의 .txt만 올려 주세요.",
      '단톡방도 같은 방법으로 내보낼 수 있어요.',
    ],
  ),
  MaterialGuide(
    kind: 'sms',
    title: '문자',
    icon: Icons.sms_outlined,
    summary: '휴대폰 문자는 파일로 꺼내는 기본 기능이 없어요. 남기고 싶은 문자를 복사해서 아래 칸에 붙여넣는 게 가장 쉬워요.',
    sections: [
      GuideSection('갤럭시', [
        '메시지 앱에서 그분과의 대화를 열어요.',
        "남기고 싶은 말풍선을 길게 누르고 '복사'를 눌러요. 한 번에 하나씩 복사돼요.",
        '아래 붙여넣기 칸에 붙여넣어요. 여러 개면 이어서 붙여넣으면 돼요.',
      ], tip: "많이 옮기려면 'SMS Backup & Restore'(무료, 다른 회사 앱)로 백업한 뒤 내보내기에서 CSV나 HTML을 골라 올려요. "
          '기본 백업 파일(.xml)도 올릴 수 있어요.'),
      GuideSection('아이폰', [
        "메시지에서 말풍선을 길게 누르고 '복사'를 눌러요.",
        '아래 붙여넣기 칸에 붙여넣어요.',
      ], tip: '대화 전체를 파일로 꺼내는 기본 기능은 없어요. 컴퓨터 프로그램(iMazing, 유료)을 쓰면 글 파일로 꺼낼 수 있어요.'),
    ],
    notes: [
      '스마트 스위치·삼성 클라우드 백업은 복원 전용이라 올릴 수 없어요.',
      '통신사 앱(클라우드 베리·올레 고객센터·U+ box)으로 백업해 엑셀로 받을 수 있는 경우도 있어요. 기종마다 달라요.',
    ],
  ),
  MaterialGuide(
    kind: 'email',
    title: '메일',
    icon: Icons.mail_outline_rounded,
    summary: '컴퓨터로 메일 사이트에 들어가 메일을 한 통씩 파일로 저장해 올려요. 휴대폰에서는 본문을 복사해 붙여넣어요.',
    sections: [
      GuideSection('Gmail', [
        '컴퓨터에서 gmail.com에 들어가 메일을 열어요.',
        "오른쪽 위 더보기(⋮) → '메일 다운로드'를 눌러요.",
        '받은 .eml 파일을 올려요. 여러 통이면 한 번에 골라 올려도 돼요.',
      ], tip: "한꺼번에 받으려면 takeout.google.com에서 'Gmail'만 고르고, '모든 메일 데이터 포함'에서 그분 메일이 "
          '들어 있는 라벨(메일함)만 골라요 → 다음 단계 → 내보내기 생성 → 메일로 온 링크에서 받아 압축을 풀고, '
          '안에 있는 .mbox 파일을 올려요. 파일 하나는 30MB까지라 메일함 전체는 너무 커요. '
          '준비하는 데 몇 분에서 며칠 걸리고, 링크는 약 7일 뒤 닫혀요.'),
      GuideSection('네이버 메일', [
        '컴퓨터에서 mail.naver.com에 들어가 메일을 열어요.',
        "'더보기' → 'PC에 저장하기'를 눌러요.",
        '받은 .eml 파일을 올려요.',
      ]),
      GuideSection('다음 메일 · 그 밖의 메일', [
        '메일을 열고 본문을 끌어서 골라 복사해요.',
        '아래 붙여넣기 칸에 붙여넣어요.',
      ]),
    ],
  ),
  MaterialGuide(
    kind: 'blog',
    title: '블로그',
    icon: Icons.article_outlined,
    summary: '블로그 글은 본문을 복사해 붙여넣는 게 가장 쉬워요. 티스토리는 백업 파일(.html)도 올릴 수 있어요.',
    sections: [
      GuideSection('네이버 블로그', [
        '컴퓨터에서 글을 열고 본문을 끌어서 골라요.',
        'Ctrl + C로 복사해 아래 칸에 붙여넣어요.',
      ], tip: '네이버 블로그 백업은 PDF로 나와서 그대로는 올릴 수 없어요.'),
      GuideSection('티스토리', [
        "관리 → 블로그 → '데이터 관리' → '백업하기'를 눌러요.",
        '받은 압축 파일을 풀어요. 글마다 폴더가 하나씩 있어요.',
        '폴더 안의 .html 파일을 올려요. 여러 개를 한 번에 골라도 돼요.',
      ], tip: '메뉴 이름은 바뀌었을 수 있어요. 안 보이면 글을 복사해 붙여넣어 주세요.'),
      GuideSection('브런치 · 그 밖의 블로그', [
        '글을 열고 본문을 복사해요.',
        '아래 칸에 붙여넣어요.',
      ]),
    ],
  ),
  MaterialGuide(
    kind: 'diary',
    title: '일기·메모',
    icon: Icons.menu_book_outlined,
    summary: '휴대폰 메모나 일기 앱의 글을 파일로 저장하거나 복사해 올려요.',
    sections: [
      GuideSection('삼성 노트', [
        '노트를 열고 오른쪽 위 더 보기(⋮)를 눌러요.',
        "'파일로 저장' → '텍스트 파일'을 골라 저장해요.",
        '저장된 .txt 파일을 올려요.',
      ], tip: '손글씨는 먼저 글자로 바꿔 주세요. 노트는 하나씩 저장돼요.'),
      GuideSection('아이폰 메모', [
        "최신 아이폰(iOS 26 이상): 메모를 열고 공유 → 'Markdown으로 내보내기' → '파일에 저장'. 항목이 안 보이면 '더 보기'를 먼저 눌러요.",
        '저장된 .md 파일을 올려요.',
        '그보다 예전 아이폰: 본문을 길게 눌러 전체 선택 → 복사 → 아래 칸에 붙여넣기.',
      ]),
      GuideSection('맥 메모', [
        "메모를 열고 '파일' → '다음으로 내보내기' → 'Markdown'을 골라요.",
        '저장된 .md 파일을 올려요.',
      ]),
      GuideSection('Google Keep', [
        "takeout.google.com에서 'Keep'만 고르고 '다음 단계' → '내보내기 생성'을 눌러요.",
        '메일로 온 링크에서 받아 압축을 풀어요.',
        'Keep 폴더의 .html 파일을 올려요.',
      ]),
      GuideSection('일기 앱', [
        "Daylio: 더 보기 → '내보내기(Export Entries)' → CSV로 받아 올려요.",
        '그 밖의 앱(하루콩 등): 글을 복사해 아래 칸에 붙여넣어요.',
      ]),
      GuideSection('손으로 쓴 일기·편지', [
        '사진을 찍고, 휴대폰의 \'사진 속 글자 복사\'(텍스트 인식)로 글을 복사해요.',
        '아래 칸에 붙여넣어요. 읽기 어려운 글씨는 직접 옮겨 적어 주세요.',
      ]),
    ],
  ),
  MaterialGuide(
    kind: 'sns',
    title: 'SNS',
    icon: Icons.public_rounded,
    summary: '페이스북·인스타그램은 내 정보 내보내기에서 HTML로 받아 올려요. 한두 개면 글을 복사해 붙여넣어요.',
    sections: [
      GuideSection('페이스북', [
        '프로필 사진 → 설정 및 개인정보 → 설정 → Meta 계정 센터로 가요.',
        "'내 정보 및 권한' → '내 정보 내보내기' → '내보내기 만들기'를 눌러요.",
        "'기기로 내보내기'를 고르고, 정보는 '게시물', 형식은 HTML로 골라 '내보내기 시작'.",
        '알림·메일로 온 파일을 4일 안에 받아 압축을 풀고, 게시물 .html 파일을 올려요.',
      ]),
      GuideSection('인스타그램', [
        '프로필 → ☰ → 계정 센터로 가요.',
        "'내 정보 및 권한' → '내 정보 내보내기' → '내보내기 만들기' → '기기로 내보내기'.",
        '형식은 HTML로 골라요. 받은 압축을 풀고 .html 파일을 올려요.',
      ]),
    ],
  ),
  MaterialGuide(
    kind: 'other',
    title: '그 밖의 글',
    icon: Icons.notes_rounded,
    summary: '편지, 가족이 기억하는 말씀, 직접 정리한 이야기처럼 글로 된 것은 무엇이든 괜찮아요.',
    sections: [
      GuideSection('올리는 방법', [
        '메모장 같은 글 파일(.txt 등)을 올려요.',
        '파일이 없으면 아래 칸에 그대로 적거나 붙여넣어요.',
      ], tip: 'PDF·워드·사진은 내용을 복사해 붙여넣어 주세요.'),
    ],
  ),
];

MaterialGuide guideFor(String kind) =>
    materialGuides.firstWhere((g) => g.kind == kind, orElse: () => materialGuides.last);
