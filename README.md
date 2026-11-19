# 텔레그램 그룹 자동 운영봇

봇: [@DDGNOTE_BOT](https://t.me/DDGNOTE_BOT)

관리자 화면에서 예약 일정의 제목, 시각, 요일, 멘트, 이미지, 채팅 열기/닫기를 등록·수정·삭제합니다. 반복 공지와 입장 환영 기능을 지원합니다.

## Railway 설정

Dockerfile로 서비스 배포 후 Railway Variables에 아래 값을 설정합니다.

```env
BOT_TOKEN=BotFather에서 받은 토큰
GROUP_CHAT_ID=-100으로 시작하는 그룹 ID
ADMIN_USER=admin
ADMIN_PASSWORD=긴 랜덤 비밀번호
DATA_DIR=/data
TIMEZONE=Asia/Seoul
```

Railway Volume을 `/data`에 마운트하여 DB와 이미지 파일을 보존합니다. **단일 인스턴스, 단일 워커**로 실행하세요.

그룹에서 봇을 관리자로 지정하고 **멤버 제한 권한**을 활성화해야 채팅 열기·닫기가 작동합니다. `getUpdates` 폴링을 사용하므로 웹훅과 중복 실행하지 마세요.

이미지 1~7은 기본 일정의 표시 이름이며, 실제 전송 이미지는 관리자에서 업로드 후 일정에 연결합니다.

## 동작 확인

- `/health`: 서버 상태
- `/`: 관리자 로그인 및 설정 화면 (HTTPS 필수)

실제 봇 전송은 환경변수 등록과 그룹 관리자 권한 설정 후에만 가능합니다.
