# 강의록 주석 PDF 생성기

PDF 강의록과 페이지별 전사문을 업로드하면, 문제/모아보기 페이지를 건너뛰고 전사문과 간단 메모가 붙은 PDF를 생성합니다.

## 로컬 실행

```bash
pip install -r requirements.txt
python app.py
```

브라우저에서 `http://127.0.0.1:10000` 접속

## Render 배포

1. 이 폴더를 GitHub에 업로드
2. Render에서 `New +` → `Web Service`
3. 저장소 연결
4. build command: `pip install -r requirements.txt`
5. start command: `gunicorn app:app`

또는 `render.yaml`이 있으면 자동 인식됩니다.

## 입력 형식 예시

```text
00:00

TalkFile_xxx.pdf
·
1페이지
출석 체크 다 되셨죠?

01:48

TalkFile_xxx.pdf
·
2페이지
먼저 복막과 peritoneum에 대해서...
```

## 참고

- 한글 폰트는 서버/컴퓨터의 시스템 폰트를 자동 탐색합니다.
- 환경에 한글 폰트가 전혀 없으면 PDF에 한글이 깨질 수 있습니다.
- 문제 페이지, 모아보기 페이지, 표지 페이지는 자동으로 건너뜁니다.
