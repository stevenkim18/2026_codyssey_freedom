"""Deterministic builder for the independent service test set DRAFT (v1).

Every document is an authored scenario template filled with seeded synthetic
values. Gold offsets are computed while rendering, so spans are exact by
construction (and re-verified by ``service_cases.validate``). All values are
synthetic: example/documentation domains and IP ranges, fictional road and
company names, random digit strings. No real personal data or credentials.

Documents are *draft*: ``review_status`` is ``needs_review`` until a person has
reviewed the gold; nothing here claims the set is frozen. This set must never
be used for dev tuning; tuning needs a separate set 
a differently seeded, differently named file that shares no template ids).

    python -m model.evaluation.service_testset_builder --output-dir model/evaluation/service_testset
"""
from collections import Counter
from pathlib import Path
import argparse
import hashlib
import json
import platform
import random
import re

GENERATOR_VERSION = 'service-testset-builder-v1'
DATASET_VERSION = 'service-test-v1-draft'
SEEDS = {'final': 20261031}
MARK = re.compile(r'⟦([A-Z0-9_]+)(?::(\w+))?(?:\|(\w+))?⟧')
ENTITY_KINDS = ('PERSON', 'EMAIL_ADDRESS', 'KR_PHONE_NUMBER', 'KR_BANK_ACCOUNT', 'CREDIT_CARD', 'KR_RRN',
                'KR_LOCATION', 'DATE_OF_BIRTH', 'KR_DRIVER_LICENSE', 'KR_PASSPORT', 'KR_NHIS', 'KR_VEHICLE_PLATE',
                'USERNAME', 'PERSONAL_ATTRIBUTE', 'ORGANIZATION', 'URL', 'IP_ADDRESS', 'NRP')

SURNAMES = '김이박최정강조윤장임한오서신권황안송류홍전고문양손배백허남심노하곽성차주우구민나진지엄채원천방공'
GIVEN = ['가온', '나래', '다솜', '라온', '마루', '바다', '사랑', '아름', '자람', '차오', '하늘', '새봄', '도윤', '서준', '예준',
         '지안', '하린', '서아', '유진', '민재', '수빈', '지호', '채원', '태오', '은비', '시온', '건우', '소율', '이안', '로하']
LATIN = ['sample', 'demo', 'testuser', 'mailbox', 'person', 'contact', 'hello', 'account', 'staff', 'guest', 'alpha', 'beta']
ROADS = ['가상로', '시험길', '예시대로', '샘플로', '테스트길', '가나로', '다라길']
DISTRICTS = ['가상구', '시험구', '예시구', '샘플군', '테스트시']
CITIES = ['가상특별시', '시험광역시', '예시도']
COMPANIES = ['가나다소프트', '라마바전자', '사아자물류', '차카타바이오', '파하솔루션', '한울가상테크', '누리샘플금융', '미래예시건설']
PLAIN_ORG_SUFFIX = ['(주)', '주식회사 ', '']
NRP = ['몽골 국적', '베트남인', '불교 신자', '가상당 당원', '무슬림', '재일교포', '캐나다 국적자', '천주교 신자']
ATTRIBUTES = ['당뇨병 환자', '휠체어 사용자', '초등학교 교사', 'AB형', '키 172cm', '시각장애 3급', '임신 20주', '석사 학위 소지',
              '태권도 3단', '채식주의자', '장기 휴직 중인 간호사', '체중 68kg']
USER_STEM = ['river', 'cloud', 'pixel', 'maple', 'tiger', 'orbit', 'lemon', 'ocean', 'comet', 'panda', 'forest', 'rocket']
DOMAINS = ['example.com', 'example.org', 'example.net', 'mail.example.com', 'corp.example.org']
HOSTS = ['example.com', 'example.org', 'docs.example.net', 'portal.example.com']
PLATE_MID = list('가나다라마거너더러머버서어저고노도로모보소오조구누두루무부수우주')


class Values:
    def __init__(self, rng):
        self.r = rng

    def digits(self, n):
        return ''.join(str(self.r.randrange(10)) for _ in range(n))

    def PERSON(self, style=None):
        return self.r.choice(SURNAMES) + self.r.choice(GIVEN)

    def EMAIL_ADDRESS(self, style=None):
        return f'{self.r.choice(LATIN)}{self.r.choice(["", ".", "_"])}{self.r.choice(USER_STEM)}{self.digits(self.r.choice([2, 3, 4]))}@{self.r.choice(DOMAINS)}'

    def KR_PHONE_NUMBER(self, style=None):
        style = style or self.r.choice(['hy', 'hy', 'hy', 'compact', 'dot', 'intl', 'land'])
        mid, last = self.digits(4), self.digits(4)
        return {'hy': f'010-{mid}-{last}', 'compact': f'010{mid}{last}', 'dot': f'010.{mid}.{last}',
                'intl': f'+82-10-{mid}-{last}', 'land': f'{self.r.choice(["02", "031", "051", "053"])}-{self.digits(3)}-{last}'}[style]

    def KR_BANK_ACCOUNT(self, style=None):
        shape = self.r.choice([(3, 3, 6), (4, 3, 6), (6, 2, 6), (3, 6, 2, 3), (14,), (12,)])
        sep = '' if len(shape) == 1 else '-'
        return sep.join(self.digits(n) for n in shape)

    def KR_RRN(self, style=None):
        year, month, day = self.r.randrange(55, 100) if self.r.random() < .6 else self.r.randrange(0, 24), self.r.randrange(1, 13), self.r.randrange(1, 29)
        gender = self.r.choice('1234' if year < 24 else '12')
        tail = gender + self.digits(6)
        return f'{year:02d}{month:02d}{day:02d}{"-" if style != "compact" else ""}{tail}'

    def CREDIT_CARD(self, style=None):
        body = self.r.choice(['400000', '555555', '411111', '378282'])
        length = 15 if body.startswith('37') else 16
        body += self.digits(length - len(body) - 1)
        total = 0
        for i, ch in enumerate(reversed(body)):
            n = int(ch) * (2 if i % 2 == 0 else 1)
            total += n - 9 if n > 9 else n
        number = body + str((10 - total % 10) % 10)
        style = style or self.r.choice(['space', 'dash', 'compact'])
        if len(number) == 15:
            groups = [number[:4], number[4:10], number[10:]]
        else:
            groups = [number[i:i + 4] for i in range(0, 16, 4)]
        return {'space': ' ', 'dash': '-', 'compact': ''}[style].join(groups)

    def KR_LOCATION(self, style=None):
        base = f'{self.r.choice(CITIES)} {self.r.choice(DISTRICTS)} {self.r.choice(ROADS)} {self.r.randrange(1, 300)}'
        return base + (f' {self.r.randrange(101, 1900)}호' if self.r.random() < .4 else '')

    def DATE_OF_BIRTH(self, style=None):
        y, m, d = self.r.randrange(1950, 2012), self.r.randrange(1, 13), self.r.randrange(1, 29)
        style = style or self.r.choice(['kr', 'iso', 'dot', 'short'])
        return {'kr': f'{y}년 {m}월 {d}일', 'iso': f'{y}-{m:02d}-{d:02d}', 'dot': f'{y}.{m:02d}.{d:02d}',
                'short': f'{y % 100:02d}년 {m}월 {d}일'}[style]

    def KR_DRIVER_LICENSE(self, style=None):
        return f'{self.r.randrange(11, 29)}-{self.digits(2)}-{self.digits(6)}-{self.digits(2)}'

    def KR_PASSPORT(self, style=None):
        return self.r.choice('MSRGD') + (self.r.choice('0123456789ABC') if self.r.random() < .25 else '') + self.digits(8)

    def KR_NHIS(self, style=None):
        return self.digits(11)

    def KR_VEHICLE_PLATE(self, style=None):
        return f'{self.r.choice([self.digits(2), self.digits(3)])}{self.r.choice(PLATE_MID)} {self.digits(4)}'

    def USERNAME(self, style=None):
        return f'{self.r.choice(USER_STEM)}{self.r.choice(["_", "-", ""])}{self.r.choice(USER_STEM)}{self.digits(self.r.choice([2, 4]))}'

    def PERSONAL_ATTRIBUTE(self, style=None):
        return self.r.choice(ATTRIBUTES)

    def ORGANIZATION(self, style=None):
        return self.r.choice(PLAIN_ORG_SUFFIX) + self.r.choice(COMPANIES)

    def URL(self, style=None):
        return f'https://{self.r.choice(HOSTS)}/{self.r.choice(["profile", "u", "docs", "files", "p"])}/{self.digits(5)}{self.r.choice(["", "?ref=mail", "#top"])}'

    def IP_ADDRESS(self, style=None):
        net = self.r.choice(['192.0.2', '198.51.100', '203.0.113'])
        return f'{net}.{self.r.randrange(1, 255)}'

    def NRP(self, style=None):
        return self.r.choice(NRP)

    # Non-PII look-alikes: never annotated.
    def ORDER(self, style=None):
        return f'ORD-{self.r.randrange(2024, 2027)}-{self.digits(6)}'

    def AMOUNT(self, style=None):
        return f'{self.r.randrange(1, 900) * 1000:,}원'

    def VERSION(self, style=None):
        return f'v{self.r.randrange(1, 5)}.{self.r.randrange(0, 20)}.{self.r.randrange(0, 30)}'

    def COMMIT(self, style=None):
        return ''.join(self.r.choice('0123456789abcdef') for _ in range(12))

    def SERIAL13(self, style=None):
        return 'SN' + self.digits(13)

    def PERCENT(self, style=None):
        return f'{self.r.randrange(1, 99)}.{self.r.randrange(0, 9)}%'

    def DATEPLAIN(self, style=None):
        return f'{self.r.randrange(2024, 2027)}-{self.r.randrange(1, 13):02d}-{self.r.randrange(1, 29):02d}'

    def PORT(self, style=None):
        return str(self.r.randrange(3000, 9000))


AVOID = set()   # official surfaces to avoid; filled by --official-root before building


def render(template, values, avoid=()):
    out, entities, last, remembered = [], [], 0, {}
    if template.count('⟦') != len(MARK.findall(template)):
        raise ValueError('malformed_placeholder')
    position = 0
    for match in MARK.finditer(template):
        out.append(template[last:match.start()])
        position += match.start() - last
        kind, name, style = match.groups()
        key = (kind, name) if name else None
        if key in remembered:
            surface = remembered[key]
        else:
            for _ in range(50):
                surface = getattr(values, kind)(style)
                if surface not in avoid:
                    break
            else:
                raise ValueError('cannot_avoid_official_surface')
        if key:
            remembered[key] = surface
        if kind in ENTITY_KINDS:
            entities.append({'type': kind, 'start': position, 'end': position + len(surface), 'surface': surface})
        out.append(surface)
        position += len(surface)
        last = match.end()
    out.append(template[last:])
    text = ''.join(out)
    for e in entities:
        assert text[e['start']:e['end']] == e['surface']
    return text, entities


# (tags, template). Tags describe stress dimensions; direction is the list name.
REQUEST_PII = [
    (['particle'], '⟦PERSON:a⟧에게 보낼 안내 메일 초안을 써줘. 받는 사람 전화번호는 ⟦KR_PHONE_NUMBER⟧로 적어두면 돼.'),
    (['particle', 'boundary'], '이 사람 주민번호가 ⟦KR_RRN⟧인데 생년월일만 뽑아서 알려줘. 이름은 ⟦PERSON⟧이고 이메일은 ⟦EMAIL_ADDRESS⟧야.'),
    (['particle'], '환불은 ⟦KR_BANK_ACCOUNT⟧으로 부탁드립니다. 예금주는 ⟦PERSON⟧님입니다. 이 문구를 공손하게 다듬어 주세요.'),
    (['particle', 'boundary'], '카드번호 ⟦CREDIT_CARD⟧로 결제했는데 승인이 안 돼요. 고객센터에 보낼 문의글 만들어줘.'),
    (['multi_entity'], '고객 정보 정리해줘: 이름 ⟦PERSON⟧, 연락처 ⟦KR_PHONE_NUMBER⟧, 이메일 ⟦EMAIL_ADDRESS⟧, 주소 ⟦KR_LOCATION⟧.'),
    (['table', 'multi_entity'], '| 이름 | 전화번호 | 이메일 |\n|---|---|---|\n| ⟦PERSON⟧ | ⟦KR_PHONE_NUMBER⟧ | ⟦EMAIL_ADDRESS⟧ |\n| ⟦PERSON⟧ | ⟦KR_PHONE_NUMBER⟧ | ⟦EMAIL_ADDRESS⟧ |\n이 표를 CSV로 바꿔줘.'),
    (['json', 'multi_entity'], '{"name": "⟦PERSON⟧", "rrn": "⟦KR_RRN⟧", "phone": "⟦KR_PHONE_NUMBER|compact⟧", "tier": "gold"}\n이 JSON 스키마를 검증하는 코드를 짜줘.'),
    (['code'], 'def send_report():\n    to = "⟦EMAIL_ADDRESS⟧"\n    cc = "⟦EMAIL_ADDRESS⟧"\n    smtp = "⟦IP_ADDRESS⟧"\n    ...\n이 함수에서 하드코딩을 설정 파일로 빼줘.'),
    (['code', 'missing_type'], 'curl -u ⟦USERNAME⟧ -X POST ⟦URL⟧ -d "card=⟦CREDIT_CARD|compact⟧"\n이 명령이 왜 400을 반환하는지 설명해줘.'),
    (['csv', 'multi_entity'], 'name,rrn,account\n⟦PERSON⟧,⟦KR_RRN⟧,⟦KR_BANK_ACCOUNT⟧\n⟦PERSON⟧,⟦KR_RRN⟧,⟦KR_BANK_ACCOUNT⟧\n이 CSV에서 중복된 행을 찾는 pandas 코드를 알려줘.'),
    (['mixed_language'], 'Please translate to English: ⟦PERSON⟧ 고객님의 이메일은 ⟦EMAIL_ADDRESS⟧이고, phone is ⟦KR_PHONE_NUMBER⟧입니다.'),
    (['mixed_language', 'missing_type'], 'My passport number is ⟦KR_PASSPORT⟧ and I was born on ⟦DATE_OF_BIRTH|iso⟧. 비자 신청서 영문 문장을 완성해줘.'),
    (['missing_type'], '운전면허 ⟦KR_DRIVER_LICENSE⟧, 차량번호 ⟦KR_VEHICLE_PLATE⟧인 차주에게 보낼 과태료 안내문을 작성해줘.'),
    (['missing_type', 'particle'], '건강보험 증서 번호 ⟦KR_NHIS⟧를 가진 가입자가 ⟦ORGANIZATION⟧에 제출할 서류 목록을 알려줘.'),
    (['missing_type'], '나는 ⟦PERSONAL_ATTRIBUTE⟧이고 ⟦NRP⟧인데, 이런 사람이 지원할 수 있는 장학금을 찾아줘.'),
    (['missing_type', 'particle'], '로그인 아이디 ⟦USERNAME⟧로 접속이 안 돼요. 접속 IP는 ⟦IP_ADDRESS⟧에서 시도했습니다.'),
    (['log', 'code'], '[2026-03-04 10:22:31] WARN auth failed user=⟦USERNAME⟧ ip=⟦IP_ADDRESS⟧ ua="Mozilla/5.0"\n[2026-03-04 10:22:35] INFO retry ok ip=⟦IP_ADDRESS⟧\n이 로그에서 이상 징후를 요약해줘.'),
    (['long', 'multi_entity'], '아래는 면담 기록입니다. 핵심만 요약해 주세요.\n\n면담 대상: ⟦PERSON:p⟧ (⟦DATE_OF_BIRTH⟧생, ⟦ORGANIZATION⟧ 소속)\n연락처: ⟦KR_PHONE_NUMBER⟧ / ⟦EMAIL_ADDRESS⟧\n주소지: ⟦KR_LOCATION⟧\n\n상담 내용: 지난달부터 업무 강도가 높아져 수면 시간이 줄었다고 함. 팀장과의 면담에서 업무 재분배를 요청했으나 아직 답변을 받지 못했다고 하며, 주 2회 재택근무 제안에 대해서는 긍정적으로 검토 중이다. ⟦PERSON:p⟧ 씨는 다음 달 초에 휴가를 계획하고 있으며, 그 전에 인수인계 문서를 정리하겠다고 말했다. 추가로 건강 관련 사항으로 ⟦PERSONAL_ATTRIBUTE⟧임을 언급했고, 급여 이체 계좌 ⟦KR_BANK_ACCOUNT⟧의 변경을 요청함. 다음 면담은 3주 후로 합의함.'),
    (['long', 'table'], '회의록 정리 부탁해요.\n\n참석자: ⟦PERSON⟧, ⟦PERSON⟧, ⟦PERSON⟧\n일시: 2026년 5월 12일 오후 3시\n안건 1. 분기 매출 보고: 목표 대비 ⟦PERCENT⟧ 달성, 신규 계약 건수 증가.\n안건 2. 인프라 점검: 서버 ⟦IP_ADDRESS⟧ 교체 일정 논의, 예산 ⟦AMOUNT⟧ 승인.\n안건 3. 채용: 지원자 ⟦PERSON⟧(⟦EMAIL_ADDRESS⟧) 최종 면접 일정 조율.\n결정 사항: 다음 주 월요일까지 각자 실행 계획을 공유한다.\n액션 아이템은 표로 만들어줘.'),
    (['boundary'], '"⟦PERSON⟧","⟦KR_PHONE_NUMBER⟧","⟦EMAIL_ADDRESS⟧" 이렇게 따옴표로 감싼 값들을 파싱하는 정규식을 알려줘.'),
    (['boundary'], '연락처(⟦KR_PHONE_NUMBER⟧)와 계좌[⟦KR_BANK_ACCOUNT⟧]를 괄호 안에 넣어서 쓰고 있는데 이게 맞는 형식인가요?'),
    (['boundary', 'particle'], '😀 ⟦PERSON⟧님께 😊 ⟦KR_PHONE_NUMBER⟧로 전화드렸는데 ✅ 부재중이셨어요. 문자 문구를 만들어줘.'),
    (['markdown'], '## 신청자\n- 이름: **⟦PERSON⟧**\n- 이메일: `⟦EMAIL_ADDRESS⟧`\n- 카드: ⟦CREDIT_CARD⟧\n\n위 신청서를 보고 누락된 항목을 알려줘.'),
    (['yaml'], 'user:\n  name: ⟦PERSON⟧\n  rrn: ⟦KR_RRN⟧\n  account: ⟦KR_BANK_ACCOUNT⟧\n  home: ⟦KR_LOCATION⟧\n이 YAML을 환경변수 파일로 변환해줘.'),
    (['sql', 'code'], "INSERT INTO members (name, phone, email) VALUES ('⟦PERSON⟧', '⟦KR_PHONE_NUMBER⟧', '⟦EMAIL_ADDRESS⟧');\n이 쿼리에서 SQL 인젝션 위험이 있는지 봐줘."),
    (['particle', 'boundary'], '⟦PERSON⟧은(는) ⟦DATE_OF_BIRTH⟧에 태어났고, ⟦ORGANIZATION⟧에서 근무합니다. 자기소개서 첫 문단을 써줘.'),
    (['particle'], '⟦ORGANIZATION⟧의 담당자 ⟦PERSON⟧과(와) 통화한 내용을 정리했어: 연락처는 ⟦KR_PHONE_NUMBER⟧, 팩스는 ⟦KR_PHONE_NUMBER|land⟧.'),
    (['missing_type', 'particle'], '차량 ⟦KR_VEHICLE_PLATE⟧이(가) 어제 ⟦KR_LOCATION⟧ 인근에서 주차 위반으로 적발됐대. 이의신청서를 써줘.'),
    (['mixed_language', 'code'], 'const user = { name: "⟦PERSON⟧", email: "⟦EMAIL_ADDRESS⟧", card: "⟦CREDIT_CARD⟧" };\n// 이 객체를 console.log 하지 않고 안전하게 디버깅하는 방법?'),
    (['multi_entity', 'particle'], '엄마 ⟦PERSON⟧ 주민번호 ⟦KR_RRN⟧랑 아빠 ⟦PERSON⟧ 주민번호 ⟦KR_RRN⟧로 가족관계증명서 신청하는 방법 알려줘.'),
    (['missing_type'], '⟦USERNAME⟧ 계정이 ⟦URL⟧ 에서 유출된 것 같아요. 어떻게 대응해야 하나요?'),
    (['boundary', 'particle'], '입금은 ⟦KR_BANK_ACCOUNT⟧→⟦KR_BANK_ACCOUNT⟧으로 이체됐다고 하는데, 중간 계좌를 거친 건가요?'),
    (['hard_digits', 'boundary'], '주문번호 ⟦ORDER⟧의 결제 카드 ⟦CREDIT_CARD⟧가 ⟦AMOUNT⟧ 승인됐는데 환불 절차를 알려줘. 일련번호는 ⟦SERIAL13⟧야.'),
]
RESPONSE_PII = [
    (['particle'], '말씀하신 대로 ⟦PERSON⟧님께 회신 초안을 작성했습니다. 연락 가능한 번호 ⟦KR_PHONE_NUMBER⟧을 하단에 넣었어요.'),
    (['multi_entity'], '조회 결과입니다. 이름: ⟦PERSON⟧ / 주민등록번호: ⟦KR_RRN⟧ / 이메일: ⟦EMAIL_ADDRESS⟧ / 주소: ⟦KR_LOCATION⟧.'),
    (['table', 'multi_entity'], '요청하신 표입니다.\n\n| 번호 | 이름 | 계좌 |\n|---|---|---|\n| 1 | ⟦PERSON⟧ | ⟦KR_BANK_ACCOUNT⟧ |\n| 2 | ⟦PERSON⟧ | ⟦KR_BANK_ACCOUNT⟧ |\n| 3 | ⟦PERSON⟧ | ⟦KR_BANK_ACCOUNT⟧ |'),
    (['json'], '```json\n{\n  "customer": {"name": "⟦PERSON⟧", "email": "⟦EMAIL_ADDRESS⟧", "card": "⟦CREDIT_CARD⟧"},\n  "status": "active"\n}\n```\n위와 같이 구성하면 됩니다.'),
    (['code'], '```python\nimport smtplib\nsender = "⟦EMAIL_ADDRESS⟧"\nserver = smtplib.SMTP("⟦IP_ADDRESS⟧", ⟦PORT⟧)\n```\n보내는 사람 주소는 환경변수로 옮기는 것을 권장합니다.'),
    (['particle', 'boundary'], '확인했습니다. ⟦KR_PHONE_NUMBER⟧로 인증번호를 발송했고, ⟦EMAIL_ADDRESS⟧으로도 안내 메일을 보냈습니다.'),
    (['particle'], '고객님의 환급금은 ⟦KR_BANK_ACCOUNT⟧으로 입금될 예정이며, 예금주 ⟦PERSON⟧님과 일치하는지 확인해 주세요.'),
    (['boundary'], '입력하신 카드번호는 ⟦CREDIT_CARD⟧, 유효기간은 확인되지 않았습니다. 번호 형식(⟦CREDIT_CARD|compact⟧)은 올바릅니다.'),
    (['mixed_language'], 'Sure! Here is the English version: "Mr. ⟦PERSON⟧ can be reached at ⟦KR_PHONE_NUMBER⟧ or ⟦EMAIL_ADDRESS⟧." 한국어 원문과 함께 보관하세요.'),
    (['mixed_language', 'missing_type'], 'Your visa form lists passport ⟦KR_PASSPORT⟧, driver license ⟦KR_DRIVER_LICENSE⟧ and 건강보험 번호 ⟦KR_NHIS⟧. 세 항목 모두 영문 표기와 일치해야 합니다.'),
    (['missing_type'], '프로필을 요약하면, ⟦PERSON⟧님은 ⟦PERSONAL_ATTRIBUTE⟧이며 ⟦NRP⟧입니다. 이런 민감정보는 외부로 보내지 않는 것이 좋습니다.'),
    (['missing_type', 'particle'], '차량번호 ⟦KR_VEHICLE_PLATE⟧의 소유자는 ⟦PERSON⟧으로 조회됩니다. 등록지는 ⟦KR_LOCATION⟧입니다.'),
    (['missing_type'], '접속 기록: 계정 ⟦USERNAME⟧이(가) ⟦IP_ADDRESS⟧에서 ⟦URL⟧ 로 접근했습니다.'),
    (['long', 'multi_entity'], '요약 보고서입니다.\n\n1. 개요\n지난 분기 고객 상담 건 중 개인정보 노출 가능성이 있는 사례를 점검했습니다. 총 건수와 비율은 별첨 표를 참고하세요.\n\n2. 사례\n사례 A: ⟦PERSON:a⟧ 고객(⟦KR_PHONE_NUMBER⟧)이 상담 중 ⟦KR_RRN⟧를 직접 입력했습니다. 상담원이 즉시 마스킹했지만 기록 시스템에는 원문이 남아 있었습니다.\n사례 B: ⟦PERSON:b⟧ 고객은 ⟦EMAIL_ADDRESS⟧로 ⟦CREDIT_CARD⟧ 카드 사진을 보냈고, 메일 서버에서 첨부파일이 격리되었습니다.\n사례 C: ⟦ORGANIZATION⟧ 제휴 담당 ⟦PERSON:c⟧ 과장이 ⟦KR_BANK_ACCOUNT⟧를 공유 채널에 게시했습니다.\n\n3. 조치\n모든 사례에서 해당 메시지를 삭제하고 고객에게 재발 방지 안내를 발송했으며, 입력 단계에서 주민번호 패턴을 차단하도록 개선 요청을 등록했습니다. ⟦PERSON:a⟧ 고객의 재안내는 ⟦DATE_OF_BIRTH⟧ 생일 이벤트와 무관하게 별도로 진행합니다.'),
    (['long', 'log'], '로그 분석 결과입니다.\n10:01:02 login ok user=⟦USERNAME⟧ ip=⟦IP_ADDRESS⟧\n10:01:09 profile read email=⟦EMAIL_ADDRESS⟧\n10:02:44 payment card=⟦CREDIT_CARD|compact⟧ amount=⟦AMOUNT⟧ order=⟦ORDER⟧\n10:03:11 export phone=⟦KR_PHONE_NUMBER⟧\n10:05:57 logout ip=⟦IP_ADDRESS⟧\n위 기록에서 카드번호와 전화번호가 평문으로 남아 있으므로 로깅 필터가 필요합니다.'),
    (['markdown'], '### 확인된 정보\n* **성명**: ⟦PERSON⟧\n* **생년월일**: ⟦DATE_OF_BIRTH⟧\n* **연락처**: ⟦KR_PHONE_NUMBER⟧\n\n정보가 맞는지 확인해 주세요.'),
    (['yaml'], '```yaml\ncontact:\n  person: ⟦PERSON⟧\n  email: ⟦EMAIL_ADDRESS⟧\n  phone: "⟦KR_PHONE_NUMBER⟧"\n  org: ⟦ORGANIZATION⟧\n```\n이렇게 작성하시면 됩니다.'),
    (['sql', 'code'], "```sql\nSELECT * FROM customers WHERE rrn = '⟦KR_RRN⟧' OR phone = '⟦KR_PHONE_NUMBER⟧';\n```\n이 쿼리는 인덱스를 사용하지 않아 느릴 수 있습니다."),
    (['particle', 'boundary'], '⟦PERSON⟧은(는) ⟦DATE_OF_BIRTH⟧에 출생했고 현재 ⟦ORGANIZATION⟧에서 근무 중이라고 안내되어 있습니다.'),
    (['boundary'], '"⟦PERSON⟧" <⟦EMAIL_ADDRESS⟧> 형식으로 헤더를 만들면 됩니다. 전화는 (⟦KR_PHONE_NUMBER⟧) 로 병기하세요.'),
    (['multi_entity', 'particle'], '가족관계 확인: 모친 ⟦PERSON⟧(⟦KR_RRN⟧)와 부친 ⟦PERSON⟧(⟦KR_RRN⟧)의 정보가 일치합니다.'),
    (['boundary', 'particle'], '이체 경로는 ⟦KR_BANK_ACCOUNT⟧에서 ⟦KR_BANK_ACCOUNT⟧으로 확인되며 수수료는 없습니다.'),
    (['csv'], '정리한 CSV입니다.\nname,email,phone\n⟦PERSON⟧,⟦EMAIL_ADDRESS⟧,⟦KR_PHONE_NUMBER⟧\n⟦PERSON⟧,⟦EMAIL_ADDRESS⟧,⟦KR_PHONE_NUMBER⟧'),
    (['hard_digits'], '결제 내역: 주문 ⟦ORDER⟧, 금액 ⟦AMOUNT⟧, 카드 ⟦CREDIT_CARD⟧, 장비 일련번호 ⟦SERIAL13⟧, 빌드 ⟦COMMIT⟧.'),
    (['mixed_language', 'code'], '```js\nfetch("⟦URL⟧", { headers: { "X-User": "⟦USERNAME⟧" } })\n```\nThe request goes out from ⟦IP_ADDRESS⟧ — 보안 정책상 허용 대역인지 확인하세요.'),
    (['missing_type'], '사원 기록에 ⟦PERSONAL_ATTRIBUTE⟧ 항목이 있고 소속은 ⟦ORGANIZATION⟧, 국적 정보는 ⟦NRP⟧로 되어 있습니다.'),
]
REQUEST_NORMAL = [
    (['normal', 'hard_digits'], '택배 송장 ⟦SERIAL13⟧의 배송 단계를 한국어로 설명하는 안내문을 써줘. 배송비는 ⟦AMOUNT⟧이야.'),
    (['normal', 'code', 'log'], 'ERROR ⟦DATEPLAIN⟧ build ⟦COMMIT⟧ failed: exit code 2 (⟦PERCENT⟧ tests passed)\n이 CI 실패 로그에서 원인을 추정해줘.'),
    (['normal', 'json', 'hard_digits'], '{"order": "⟦ORDER⟧", "amount": "⟦AMOUNT⟧", "version": "⟦VERSION⟧"}\n이 JSON이 주문 스키마에 맞는지 확인해줘.'),
    (['normal', 'table', 'mixed_language'], '| endpoint | latency | success |\n|---|---|---|\n| /health | 12ms | ⟦PERCENT⟧ |\n| /search | 140ms | ⟦PERCENT⟧ |\nWhich endpoint should we optimize first?'),
    (['normal', 'particle'], '버전 ⟦VERSION⟧을 배포하기 전에 점검할 항목을 체크리스트로 만들어줘. 예정일은 ⟦DATEPLAIN⟧이야.'),
    (['normal', 'code'], 'docker run -p ⟦PORT⟧:80 --name web nginx:latest\n이 컨테이너가 시작되지 않을 때 확인할 순서를 알려줘.'),

    (['normal'], '파이썬에서 리스트를 중복 없이 정렬하는 가장 간단한 방법을 알려줘.'),
    (['normal'], '다음 주 팀 회식 장소로 고를 만한 메뉴 유형을 세 가지만 추천해줘.'),
    (['normal', 'code'], 'for i in range(10):\n    print(i * i)\n이 코드를 한 줄 리스트 컴프리헨션으로 바꿔줘.'),
    (['normal', 'json'], '{"id": 7, "status": "ok", "items": [1, 2, 3]}\n이 JSON의 items 합계를 구하는 jq 명령을 알려줘.'),
    (['normal', 'hard_digits'], '주문번호 ⟦ORDER⟧의 배송이 지연되고 있어요. 결제 금액은 ⟦AMOUNT⟧이고 장비 일련번호는 ⟦SERIAL13⟧입니다. 문의글을 정리해줘.'),
    (['normal', 'hard_digits', 'code'], 'git checkout ⟦COMMIT⟧ 후에 ⟦VERSION⟧ 태그를 만들었는데 배포 서버 포트 ⟦PORT⟧가 안 열려요. 원인을 추정해줘.'),
    (['normal', 'table'], '| 분기 | 매출 | 증감 |\n|---|---|---|\n| 1분기 | ⟦AMOUNT⟧ | ⟦PERCENT⟧ |\n| 2분기 | ⟦AMOUNT⟧ | ⟦PERCENT⟧ |\n이 표를 보고 추세를 설명해줘.'),
    (['normal', 'mixed_language'], 'Explain the difference between 조사 and 어미 in Korean grammar with three short examples.'),
    (['normal', 'particle'], '철수에게 보낼 생일 축하 메시지를 재치 있게 써줘. 이름은 일부러 가볍게 불러줘도 돼.'),
    (['normal', 'long'], '다음 글을 세 문장으로 요약해줘.\n\n도시의 공원은 시민들의 휴식 공간이자 생태 거점이다. 최근에는 기후 변화에 대응하기 위해 그늘막과 빗물 저류 시설을 갖춘 공원이 늘고 있다. 전문가들은 공원 면적보다 접근성과 연결성이 도시 열섬 완화에 더 중요하다고 지적한다. 또한 지역 주민이 직접 관리에 참여하는 방식이 유지 비용을 낮추고 이용 만족도를 높인다는 연구 결과도 있다. 반면 일부 지자체는 예산 부족으로 관리가 소홀해 시설이 노후화되고 있다. 이러한 격차를 줄이기 위해 광역 단위의 지원 체계가 필요하다는 의견이 제기된다.'),
    (['normal', 'code', 'sql'], 'SELECT category, COUNT(*) FROM products GROUP BY category HAVING COUNT(*) > 10;\n이 쿼리의 실행 계획을 해석하는 방법을 알려줘.'),
    (['normal', 'log'], '[2026-03-04 10:22:31] INFO job=⟦COMMIT⟧ finished in ⟦PERCENT⟧ of budget\n[2026-03-04 10:22:35] WARN retry queue length=⟦PORT⟧\n이 로그가 정상인지 판단해줘.'),
    (['normal', 'markdown'], '## 학습 계획\n- 월: 알고리즘 ⟦PERCENT⟧ 진도\n- 화: 영어 회화\n- 수: 운동\n이 계획에 빠진 요소가 있을까?'),
    (['normal', 'mixed_language', 'code'], 'TypeError: cannot read properties of undefined (reading "map") 에러가 났는데, 배열이 비어 있을 때 어떻게 방어하죠?'),
    (['normal', 'boundary'], '"가나다", "라마바", "사아자" 이 세 단어를 따옴표째로 파싱하는 정규식을 알려줘.'),
    (['normal', 'hard_digits'], '버전 ⟦VERSION⟧에서 ⟦VERSION⟧로 올리면 호환성 문제가 있을까? 빌드 번호는 ⟦DATEPLAIN⟧ 기준이야.'),
    (['normal', 'particle'], '내일 오전 아홉 시에 시작하는 면접을 앞두고 있어. 서울에서 대전까지 이동 시간을 고려한 준비 일정을 짜줘.'),
    (['normal', 'yaml'], 'service:\n  name: demo\n  replicas: 3\n  port: ⟦PORT⟧\n이 YAML에서 헬스체크 설정을 추가해줘.'),
    (['normal'], '이 문장의 맞춤법을 고쳐줘: 어제 친구랑 영화를 봤는데 너무 재밋어서 또 보고 싶어.'),
    (['normal', 'hard_digits', 'table'], '| 장비 | 일련번호 | 상태 |\n|---|---|---|\n| 라우터 | ⟦SERIAL13⟧ | 정상 |\n| 스위치 | ⟦SERIAL13⟧ | 점검 |\n상태가 점검인 장비만 따로 정리해줘.'),
    (['normal', 'hard_names'], '가나다 상사와 라마바 약품의 합병 소식에 대한 중립적인 뉴스 요약을 부탁해. 개인 이름은 나오지 않아.'),
    (['normal', 'mixed_language'], '이 영어 문장을 자연스러운 한국어로 번역해줘: "The migration finished without downtime, and all checks passed."'),
    (['normal', 'long', 'particle'], '아래 공지문을 더 간결하게 고쳐줘.\n\n안녕하세요. 이번 주 금요일 오후 두 시부터 네 시까지 사내 네트워크 점검이 진행될 예정입니다. 점검 시간 동안에는 일부 내부 시스템 접속이 원활하지 않을 수 있으니 업무 일정에 참고해 주시기 바랍니다. 작업이 예정보다 일찍 끝나면 별도 공지 없이 정상화될 수 있으며, 문제가 지속되면 헬프데스크 게시판에 문의를 남겨 주세요. 불편을 드려 죄송합니다. 협조해 주셔서 감사합니다.'),
    (['normal', 'code', 'boundary'], 'const re = /^[A-Z]{2}\\d{4}$/;\nconsole.log(re.test("AB1234"));\n이 정규식이 허용하는 문자열의 예를 더 알려줘.'),
]
RESPONSE_NORMAL = [
    (['normal', 'hard_digits'], '송장 ⟦SERIAL13⟧은 현재 간선 상차 단계입니다. 배송비 ⟦AMOUNT⟧은 이미 결제되었고 영업일 기준 하루 안에 도착할 예정입니다.'),
    (['normal', 'code', 'log'], '로그 요약: 빌드 ⟦COMMIT⟧이 ⟦DATEPLAIN⟧에 실패했습니다. 테스트 ⟦PERCENT⟧ 통과 후 린트 단계에서 종료 코드 2가 발생했습니다.'),
    (['normal', 'json', 'hard_digits'], '```json\n{"order": "⟦ORDER⟧", "amount": "⟦AMOUNT⟧", "version": "⟦VERSION⟧", "valid": true}\n```\n스키마에 맞습니다.'),
    (['normal', 'table', 'mixed_language'], '| endpoint | 권장 조치 |\n|---|---|\n| /search | 쿼리 캐시 도입 (현재 성공률 ⟦PERCENT⟧) |\n| /health | 유지 |\nThe /search endpoint is the better first target.'),
    (['normal', 'particle'], '버전 ⟦VERSION⟧ 배포 체크리스트입니다. 1) 마이그레이션 시뮬레이션 2) 롤백 계획 3) 모니터링 알림 확인. 예정일 ⟦DATEPLAIN⟧ 이전에 완료하세요.'),
    (['normal', 'code'], '컨테이너가 시작되지 않으면 로그를 먼저 보세요: `docker logs web`. 호스트 포트 ⟦PORT⟧가 이미 사용 중이면 다른 포트로 바꿔 실행하면 됩니다.'),

    (['normal'], '리스트를 중복 없이 정렬하려면 sorted(set(values))를 쓰면 됩니다. 원본 순서를 유지해야 한다면 dict.fromkeys를 사용하세요.'),
    (['normal', 'particle'], '추천 메뉴는 세 가지입니다. 첫째 한식 백반, 둘째 샤브샤브, 셋째 브런치 뷔페입니다. 인원과 예산에 따라 고르시면 됩니다.'),
    (['normal', 'code'], '```python\nprint([i * i for i in range(10)])\n```\n한 줄 컴프리헨션으로 같은 결과를 얻을 수 있습니다.'),
    (['normal', 'json'], '```json\n{"id": 7, "status": "ok", "total": 6}\n```\n합계는 jq \'.items | add\' 로 구할 수 있습니다.'),
    (['normal', 'hard_digits'], '문의하신 주문 ⟦ORDER⟧은 현재 물류센터에서 출고 대기 중이며 결제 금액 ⟦AMOUNT⟧은 정상 승인되었습니다. 장비 번호 ⟦SERIAL13⟧는 개인 식별 정보가 아닙니다.'),
    (['normal', 'hard_digits', 'code'], '체크아웃한 커밋 ⟦COMMIT⟧은 ⟦VERSION⟧ 태그와 일치합니다. 포트 ⟦PORT⟧가 열리지 않는다면 방화벽 규칙부터 확인하세요.'),
    (['normal', 'table'], '| 항목 | 값 |\n|---|---|\n| 성공률 | ⟦PERCENT⟧ |\n| 평균 비용 | ⟦AMOUNT⟧ |\n| 기준일 | ⟦DATEPLAIN⟧ |\n성공률이 개선되고 있습니다.'),
    (['normal', 'mixed_language'], '조사(postposition)는 명사 뒤에 붙고, 어미(ending)는 용언 뒤에 붙습니다. 예: 학교에(조사), 먹었다(어미).'),
    (['normal', 'long'], '요약입니다. 공원은 휴식과 생태 기능을 함께 가지며, 접근성과 연결성이 열섬 완화에 크게 기여합니다. 주민 참여 관리는 비용을 낮추고 만족도를 높이지만, 예산이 부족한 지역에서는 시설 노후화가 문제입니다. 이를 줄이기 위해 광역 단위 지원이 필요합니다. 아울러 설계 단계에서 그늘과 배수를 고려하면 유지 비용을 더 줄일 수 있습니다.'),
    (['normal', 'code', 'sql'], '```sql\nEXPLAIN SELECT category, COUNT(*) FROM products GROUP BY category;\n```\nSeq Scan이 보이면 category 인덱스를 검토하세요.'),
    (['normal', 'log'], '10:22:31 job ⟦COMMIT⟧ 완료, 예산의 ⟦PERCENT⟧ 사용. 재시도 큐 길이 ⟦PORT⟧는 임계값 이내라 정상으로 보입니다.'),
    (['normal', 'markdown'], '### 제안\n* 주말에 복습 시간을 추가하세요.\n* 수면 시간을 확보하세요.\n* 진도율 ⟦PERCENT⟧은 적절합니다.'),
    (['normal', 'mixed_language', 'code'], '배열이 undefined일 수 있으니 `(items ?? []).map(...)` 로 방어하세요. Optional chaining도 도움이 됩니다.'),
    (['normal', 'boundary'], '정규식은 "([^"]+)" 입니다. 따옴표 안의 문자열만 캡처하며 "가나다", "라마바", "사아자" 모두 매칭됩니다.'),
    (['normal', 'hard_digits'], '⟦VERSION⟧에서 ⟦VERSION⟧로의 업그레이드는 마이너 변경이라 큰 문제는 없지만 ⟦DATEPLAIN⟧ 이후 빌드부터 설정 형식이 바뀌었습니다.'),
    (['normal', 'particle'], '이동 시간은 KTX로 약 한 시간입니다. 면접 전날 일찍 잠들고 아침에는 여유 있게 출발하는 일정을 권합니다.'),
    (['normal', 'yaml'], '```yaml\nservice:\n  name: demo\n  replicas: 3\n  port: ⟦PORT⟧\n  healthcheck:\n    path: /healthz\n```\n헬스체크가 추가되었습니다.'),
    (['normal'], '수정한 문장: 어제 친구랑 영화를 봤는데 너무 재밌어서 또 보고 싶어. "재밋어서"는 "재밌어서"가 맞는 표기입니다.'),
    (['normal', 'hard_digits', 'table'], '| 장비 | 일련번호 | 상태 |\n|---|---|---|\n| 스위치 | ⟦SERIAL13⟧ | 점검 |\n점검 중인 장비는 한 대입니다.'),
    (['normal', 'hard_names'], '가나다 상사와 라마바 약품이 합병을 발표했습니다. 두 회사는 중복 사업을 정리하고 연구개발 인력을 통합할 계획이라고 밝혔습니다.'),
    (['normal', 'mixed_language'], '번역: "마이그레이션이 무중단으로 끝났고 모든 점검을 통과했습니다." 원문은 기술 보고서에서 흔한 표현입니다.'),
    (['normal', 'long', 'particle'], '수정본입니다.\n\n이번 주 금요일 오후 2~4시에 사내 네트워크를 점검합니다. 이 시간에는 일부 내부 시스템 접속이 느리거나 끊길 수 있습니다. 점검이 일찍 끝나면 따로 공지하지 않고 정상화됩니다. 문제가 계속되면 헬프데스크 게시판에 문의해 주세요. 불편을 드려 죄송하며, 협조에 감사드립니다. 추가 안내가 필요하면 담당 부서 게시글을 참고하세요.'),
    (['normal', 'code', 'boundary'], '허용 예: AB0000, ZZ9999, QX0420. 반면 ab1234(소문자)나 ABC123(자릿수 불일치)은 통과하지 못합니다.'),
]


FILLER = [
    '참고로 이번 건과 별개로, 지난 분기의 처리 현황은 내부 위키의 공유 문서에 정리되어 있으며 담당 부서는 매월 첫째 주에 갱신합니다. 갱신 시점마다 변경 이력이 남기 때문에 필요하면 이전 버전을 비교해 볼 수 있습니다.',
    '배경을 조금 더 설명하면, 이 절차는 감사 대응을 위해 도입되었고 이후 현업의 요청으로 단계가 단순해졌습니다. 다만 예외 상황에서는 여전히 수기 확인이 필요하며, 이때는 결재선이 한 단계 늘어납니다.',
    '일정 측면에서는 준비 기간이 대략 2주, 검토 기간이 1주 정도 소요됩니다. 연휴나 시스템 점검이 겹치면 지연될 수 있으므로 여유를 두고 일정을 잡는 편이 안전합니다. 일정 변경은 담당자에게 미리 알려 주세요.',
    '비용 관련해서는 별도 예산 항목이 없고 기존 운영비에서 처리합니다. 추가 인력이 필요한 경우에만 분기 말에 증액을 요청하며, 요청서에는 예상 작업량과 대체 수단의 비교를 첨부하도록 되어 있습니다.',
    '품질 점검은 샘플링 방식으로 진행하고, 발견된 문제는 심각도에 따라 즉시 조치 또는 다음 주기 반영으로 나눕니다. 반복되는 문제는 원인 분석 후 체크리스트에 항목을 추가합니다.',
    '마지막으로 문서 보관 기한은 법령과 내부 규정에 따라 다르며, 만료된 문서는 파기 대장에 기록한 뒤 폐기합니다. 보관 중인 문서에 대한 열람은 사유와 시간을 기록해야 합니다.',
]


def _instantiate(templates, direction, per_template, rng, prefix, start_index):
    docs = []
    for t_index, (tags, template) in enumerate(templates):
        count = per_template if MARK.search(template) else 1
        for k in range(count):
            if 'long' in tags:
                template_k = template + '\n\n' + '\n\n'.join(rng.sample(FILLER, 2 + k))
            else:
                template_k = template
            values = Values(rng)
            text, entities = render(template_k, values, AVOID)
            docs.append({'direction': direction, 'text': text, 'entities': entities, 'tags': sorted(set(tags)),
                         'template_id': f'{prefix}{t_index:03d}', 'instance': k})
    return docs


def build(purpose='final'):
    if purpose not in SEEDS:
        raise ValueError('unknown_purpose')
    rng = random.Random(SEEDS[purpose])
    docs = []
    docs += _instantiate(REQUEST_PII, 'request', 4, rng, 'rq-pii-', 0)
    docs += _instantiate(RESPONSE_PII, 'response', 4, rng, 'rs-pii-', 0)
    docs += _instantiate(REQUEST_NORMAL, 'request', 4, rng, 'rq-ok-', 0)
    docs += _instantiate(RESPONSE_NORMAL, 'response', 4, rng, 'rs-ok-', 0)
    return docs


def select_300(docs, rng_seed):
    """Deterministically trim to exactly 150 request + 150 response while keeping balance."""
    rng = random.Random(rng_seed)
    chosen = []
    for direction in ('request', 'response'):
        pool = [d for d in docs if d['direction'] == direction]
        normal = [d for d in pool if not d['entities']]
        pii = [d for d in pool if d['entities']]
        rng.shuffle(normal)
        rng.shuffle(pii)
        chosen += normal[:50] + pii[:100]
    return sorted(chosen, key=lambda d: (d['direction'], d['template_id'], d['instance']))


def finalize(docs, purpose):
    prefix = 'svc'
    out = []
    for i, d in enumerate(docs, 1):
        out.append({'id': f'{prefix}-{d["direction"][:3]}-{i:03d}', 'direction': d['direction'], 'text': d['text'],
                    'entities': d['entities'], 'tags': d['tags'] + (['has_pii'] if d['entities'] else []),
                    'generation': {'method': 'authored_template_seeded_synthetic_values', 'template_id': d['template_id'],
                                   'instance': d['instance'], 'generator': GENERATOR_VERSION, 'seed': SEEDS[purpose]},
                    'review_status': 'needs_review',
                    'review_notes': ['gold generated by construction; human review pending']})
    return out


def write(output_dir, purpose='final'):
    docs = finalize(select_300(build(purpose), SEEDS[purpose]), purpose)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    name = 'service-test-v1.draft.jsonl'
    path = output_dir / name
    payload = ''.join(json.dumps(d, ensure_ascii=False, sort_keys=True) + '\n' for d in docs).encode()
    with path.open('xb') as handle:
        handle.write(payload)
    return path, docs, hashlib.sha256(payload).hexdigest()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--purpose', choices=sorted(SEEDS), default='final')
    parser.add_argument('--official-root', type=Path,
                        help='Official split directory; generated values equal to its long surfaces are re-rolled')
    args = parser.parse_args(argv)
    if args.official_root:
        from .service_cases import official_index
        AVOID.update(official_index(args.official_root)[1])
    path, docs, digest = write(args.output_dir, args.purpose)
    print(json.dumps({'path': str(path), 'documents': len(docs), 'sha256': digest,
                      'python': platform.python_version(), 'generator': GENERATOR_VERSION}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
