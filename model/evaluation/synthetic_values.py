"""Corrected v2 synthetic format policy; legacy v1 remains reproducible.

These are generated examples, not verified issued identifiers. Passport format:
https://www.passport.go.kr/home/kor/onlineExperience/index.do?menuPos=61
Other ranges are deliberately conservative dataset choices from human review,
not universal validators of every historic or exceptional real-world format.
"""
from datetime import date
import random
import re

from .service_testset_builder import Values, render

ATTRIBUTES=['오른손잡이','왼손잡이','양손잡이','AB형','O형','A형','B형','적록색약',
            '야간 근무자','교대 근무자','주간 근무자','채식주의자','비건','휠체어 사용자',
            '보청기 사용자','수어 사용자','안경 착용자','콘택트렌즈 착용자','청각장애인',
            '시각장애인','전문대 졸업자','대학원 재학생','학사 학위 소지자','석사 학위 소지자']
GROUPS=['독일인','프랑스인','힌두교도','도교 신자','스페인인','포르투갈인','네덜란드인',
        '스웨덴인','핀란드인','뉴질랜드인','덴마크인','그리스인','터키인','브라질인',
        '아르헨티나인','체코인','폴란드인','오스트리아인','가톨릭 신자','개신교 신자',
        '정교회 신자','시크교도','유대교 신자','자이나교도']


class FreshValues(Values):
    def __init__(self,rng):
        super().__init__(rng)
        self._pools={}
        self.current_birth=None

    def _without_replacement(self,kind,options):
        if not self._pools.get(kind):
            self._pools[kind]=self.r.sample(options,len(options))
        return self._pools[kind].pop()

    def PERSONAL_ATTRIBUTE(self,style=None):
        return self._without_replacement('attribute',ATTRIBUTES)

    def NRP(self,style=None):
        return self._without_replacement('group',GROUPS)

    def KR_PASSPORT(self,style=None):
        if self.r.randrange(2):
            return 'M'+self.digits(8)
        return 'M'+self.digits(3)+self.r.choice('ABCDEFGHJKLMNPQRSTUVWXYZ')+self.digits(4)

    def KR_RRN(self,style=None):
        year=self.r.randrange(1960,2005)
        month,day=self.r.randrange(1,13),self.r.randrange(1,29)
        self.current_birth=date(year,month,day)
        body=f'{year%100:02d}{month:02d}{day:02d}'+self.r.choice('12' if year<2000 else '34')+self.digits(5)
        checksum=(11-sum(int(c)*w for c,w in zip(body,[2,3,4,5,6,7,8,9,2,3,4,5]))%11)%10
        return body[:6]+('' if style=='compact' else '-')+body[6:]+str(checksum)

    def DATE_OF_BIRTH(self,style=None):
        if self.current_birth:
            d=self.current_birth
            return self.r.choice([d.isoformat(),f'{d.year}년 {d.month:02d}월 {d.day:02d}일',
                                  f'{d.year} / {d.month:02d} / {d.day:02d}',f'{d.year}. {d.month}. {d.day}.'])
        return super().DATE_OF_BIRTH(style)

    def KR_DRIVER_LICENSE(self,style=None):
        region=self.r.choice([11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,28])
        return f'{region:02d}-{self.r.randrange(20,26):02d}-{self.digits(6)}-{self.digits(2)}'

    def KR_VEHICLE_PLATE(self,style=None):
        prefix=f'{self.r.randrange(1,100):02d}' if self.r.randrange(2) else str(self.r.randrange(100,700))
        return prefix+self.r.choice('가나다라마거너더러머버서어저고노도로모보소오조구누두루무부수우주')+' '+self.digits(4)

    def KR_PHONE_NUMBER(self,style=None):
        style=style or self.r.choice(['hy','compact','dot','intl','land'])
        middle=self.r.choice('23456789')+self.digits(3)
        last=self.digits(4)
        if style=='land':
            prefix=self.r.choice(['02','031','051','053'])
            return prefix+'-'+middle[:3]+'-'+last
        return {'hy':f'010-{middle}-{last}','compact':f'010{middle}{last}',
                'dot':f'010.{middle}.{last}','intl':f'+82-10-{middle}-{last}'}[style]


def josa(surface,pair):
    char=surface[-1]
    if '가'<=char<='힣':
        has_final=(ord(char)-ord('가'))%28!=0
    elif char.isdigit() and char in '0123456789':
        has_final=char in '013678'
    else:
        has_final=char.lower() in 'fhlmnrsxz'
    return pair[0] if has_final else pair[1]


def render_natural(template,values,avoid=()):
    text,entities=render(template,values,avoid=avoid)
    edits=[]
    for match in re.finditer(r'<JOSA:(은는|이가|을를|과와)>',text):
        previous=next((e for e in entities if e['end']==match.start()),None)
        if previous is None:
            raise ValueError('josa_must_follow_entity')
        edits.append((match.start(),match.end(),josa(previous['surface'],match.group(1))))
    for start,end,replacement in reversed(edits):
        text=text[:start]+replacement+text[end:]
        shift=len(replacement)-(end-start)
        for entity in entities:
            if entity['start']>=end:
                entity['start']+=shift;entity['end']+=shift
    for entity in entities:
        if text[entity['start']:entity['end']]!=entity['surface']:
            raise ValueError('josa_offset_roundtrip_failed')
    return text,entities
