"""A-only fallback for context-sensitive word splits at window edges.

Preserves the full-document word sequence. Whole whitespace groups are used
only when B's conservative planner rejects a window whose substring splits
differently. Every accepted fallback is checked for exact split equivalence,
model budget, coverage and span overlap. B production code is unchanged.
"""
import re

from dev.detector.gliner_backend import GLiNERBackend, _CoverageError


class CoverageBackend(GLiNERBackend):
    def _plan(self,text,checks_remaining):
        try:
            return super()._plan(text,checks_remaining)
        except _CoverageError:
            return self._whole_group_plan(text,checks_remaining)

    def _whole_group_plan(self,text,checks_remaining):
        tokens=self._tokens(text)
        if not tokens:return []
        ends={m.end() for m in re.finditer(r'\S+',text)}
        boundaries=[0]+[i+1 for i,t in enumerate(tokens) if t[2] in ends]
        if boundaries[-1]!=len(tokens):raise _CoverageError
        windows=[];start=0
        while start<len(tokens):
            options=[e for e in boundaries if start<e<=start+self.max_words]
            best=None;low=0;high=len(options)-1
            while low<=high:
                index=(low+high)//2;end=options[index]
                if checks_remaining[0]<=0:raise _CoverageError
                checks_remaining[0]-=1
                a,b=tokens[start][1],tokens[end-1][2]
                expected=[(word,c-a,d-a) for word,c,d in tokens[start:end]]
                fits=self._tokens(text[a:b])==expected and self._fits([t[0] for t in tokens[start:end]])
                if fits:best=end;low=index+1
                else:high=index-1
            if best is None:raise _CoverageError
            windows.append((tokens[start][1],tokens[best-1][2]))
            if len(windows)>self.max_windows:raise _CoverageError
            if best==len(tokens):break
            limit=best-self.max_span+1
            choices=[b for b in boundaries if start<b<=limit]
            if not choices:raise _CoverageError
            start=max(choices)
        return windows
