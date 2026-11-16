# 📊 Daily Ad Report Bot — 광고 성과 일일 리포트 자동화

매일 아침 8시, 여러 광고 매체(Meta · Google · TikTok)의 전날 성과를 모아 **계산하고, 이상 징후를 찾고, 읽기 쉬운 요약을 Slack으로 보내는** 봇입니다.

> 숫자는 SQL로 계산하고, LLM(DeepSeek)은 설명만 맡습니다. LLM이 쓴 요약은 발송 전에 숫자를 하나하나 대조해 검증합니다.

### 이런 메시지가 옵니다

```
📊 11/12(목) 광고 성과 요약
총 소진 6,250,242원 (전일 대비 -4.0%) · 전환 602건 · CPA 10,382원

⚠️ 확인 필요
• [critical] 유튜브_리마케팅(Google) 활성 캠페인인데 전일 데이터가 들어오지 않음
• [critical] 겨울세일_리타겟팅(Meta) 예산 대비 137% 소진 (683,730원 / 일예산 500,000원)
• [warn] 신규설치_UA(TikTok) CTR 0.37% → 7일 평균 0.83% 대비 급락

매체별
• Google: 소진 2,264,678원 (비중 36%, 전일 대비 -17.1%) · 전환 263건 · CPA 8,611원
• Meta: 소진 2,481,964원 (비중 40%, 전일 대비 +17.3%) · 전환 164건 · CPA 15,134원
• TikTok: 소진 1,503,600원 (비중 24%, 전일 대비 -9.6%) · 전환 175건 · CPA 8,592원
```
(위는 LLM 없이 만든 템플릿 리포트입니다. API 키를 넣으면 DeepSeek이 총평 문장을 써 줍니다.)

---

## 1. 전체 흐름

```
Mock 매체 API ──① 수집──▶ Adapter(매체별) ──② 적재(upsert)──▶ PostgreSQL
 (Meta/Google/TikTok,       공통 스키마로 정규화                (Supabase)
  응답 형식이 서로 다름)                                         │
                                                ③ SQL 집계 · 이상 탐지
                                                               ▼
        Slack ◀──⑥ 발송── 검증기 ◀──⑤── DeepSeek 요약 ◀──④ 계산 끝난 JSON
     (실패 시 이메일)   (숫자 대조, 실패 시 1회 재생성 → 그래도 실패면 템플릿)
```

GitHub Actions가 매일 08:00(KST)에 위 과정을 한 번 실행하고 끝납니다. 상시 서버가 없어 비용이 들지 않습니다.

| 매체 | 응답의 특이점 | Adapter가 하는 일 |
|---|---|---|
| Meta | 숫자가 문자열, 전환이 `actions[]` 배열 안에 있음 | `purchase` 액션만 꺼내 전환으로 |
| Google | 비용이 `cost_micros`(×1,000,000), 노출이 `impr` | ÷1,000,000, 필드명 통일 |
| TikTok | **UTC 시간별** 행, **USD** | +9시간 해서 KST 날짜로 다시 합산, 환율로 원화 변환 |

---

## 2. 처음 설정하기 (약 20분)

준비물: GitHub 계정, [Supabase](https://supabase.com) 계정(무료), Slack 워크스페이스, (선택) [DeepSeek API 키](https://platform.deepseek.com/api_keys)

### 2-1. Supabase 데이터베이스 만들기
1. Supabase에서 **New project** 생성 (비밀번호를 메모해 두세요)
2. 프로젝트 화면 상단 **Connect** → **Session pooler** 의 URI를 복사
   `postgresql://postgres.xxxx:[YOUR-PASSWORD]@aws-0-ap-northeast-2.pooler.supabase.com:5432/postgres`
3. `[YOUR-PASSWORD]` 부분을 실제 비밀번호로 바꿔 둡니다 → 이것이 `DATABASE_URL`
   > GitHub Actions는 IPv6 직접 연결이 안 되므로 꼭 **pooler** 주소를 쓰세요.

테이블은 파이프라인이 처음 실행될 때 자동으로 만들어집니다. (직접 만들고 싶다면 Supabase **SQL Editor**에 `sql/schema.sql` 내용을 붙여 넣고 Run)

### 2-2. Slack Webhook 만들기
1. https://api.slack.com/apps → **Create New App** → From scratch
2. **Incoming Webhooks** 켜기 → **Add New Webhook to Workspace** → 받을 채널 선택
3. `https://hooks.slack.com/services/...` 주소를 복사 → 이것이 `SLACK_WEBHOOK_URL`

### 2-3. GitHub에 비밀값 등록
저장소 **Settings → Secrets and variables → Actions → New repository secret** 에서 아래를 등록합니다.

| 이름 | 필수 | 값 |
|---|---|---|
| `DATABASE_URL` | ✅ | 2-1에서 만든 주소 |
| `SLACK_WEBHOOK_URL` | ✅ | 2-2에서 만든 주소 |
| `DEEPSEEK_API_KEY` | 선택 | 없으면 LLM 없이 템플릿 리포트로 발송 |
| `SMTP_HOST` `SMTP_PORT` `SMTP_USER` `SMTP_PASSWORD` `EMAIL_TO` | 선택 | Slack 실패 시 이메일로 대신 받기 |

> 비밀값은 저장소 파일에 절대 적지 마세요. 저장소에는 빈 양식인 `.env.example`만 올라갑니다.

### 2-4. 첫 실행
저장소 **Actions → daily-ad-report → Run workflow** 를 누르면 바로 한 번 실행됩니다.
첫 실행 때는 최근 7일치 데이터를 자동으로 채운 뒤 어제 리포트를 보냅니다. 이후로는 매일 아침 자동으로 옵니다.

---

## 3. 내 컴퓨터에서 실행해 보기

```bash
python3.11 -m venv .venv && source .venv/bin/activate
```
```bash
pip install -r requirements-dev.txt
```
```bash
cp .env.example .env
```
`.env`의 `DATABASE_URL`을 비워 두면 **내 컴퓨터 안에 임시 PostgreSQL을 자동으로 띄워** 씁니다(`.pgdata/` 폴더). Supabase 없이도 바로 해 볼 수 있습니다.

터미널 1 — 가짜 매체 API 켜기:
```bash
uvicorn mock_api.app:app --port 8000
```
터미널 2 — 데이터 적재 후 리포트 만들기:
```bash
python -m pipeline backfill --days 30
```
```bash
python -m pipeline daily --dry-run
```

| 명령 | 하는 일 |
|---|---|
| `python -m pipeline init-db` | 테이블 생성 (여러 번 실행해도 안전) |
| `python -m pipeline backfill --days 30` | 최근 30일 데이터 적재 |
| `python -m pipeline daily` | 어제 리포트 생성 → Slack 발송 |
| `python -m pipeline daily --date 2026-11-12` | 특정 날짜 리포트 |
| `python -m pipeline daily --dry-run` | 발송하지 않고 화면에만 출력 |
| `python -m pipeline daily --no-llm` | LLM 없이 템플릿 리포트 |
| `python -m mock_api.generator 14` | 최근 14일 중 어느 날 어떤 이상 징후가 심어져 있는지 보기 |

실행할 때마다 `out/report_날짜.json`(LLM에 들어간 JSON)과 `out/message_날짜.txt`(발송 메시지)가 저장됩니다.

---

## 4. 알림 기준 바꾸기 (코드 수정 없음)

`config.yaml`만 고치면 됩니다.

```yaml
alerts:
  ctr_drop:   { ratio: 0.70, severity: warn }      # 어제 CTR이 7일 평균의 70% 미만
  overspend:  { ratio: 1.20, severity: critical }  # 어제 소진액이 일예산의 120% 초과
  cpa_spike:  { ratio: 1.50, severity: warn }      # 어제 CPA가 7일 평균의 150% 초과
  missing_data: { severity: critical }             # 켜져 있는 캠페인인데 어제 데이터가 없음
```
- "알림이 너무 많다" → `ratio`를 더 엄격하게 (예: `ctr_drop` 0.70 → 0.60)
- 매체 하나를 빼고 싶다 → `media.tiktok.enabled: false`
- 환율 변경 → `fx.USD_KRW`
- LLM 모델 변경 → `llm.model` (OpenAI 호환 API라면 `llm.base_url`, `llm.api_key_env`만 바꿔 다른 회사 모델도 사용 가능)

### LLM 비용
기본 모델은 DeepSeek의 가장 싼 경량 모델 `deepseek-flash`(추론 모드 끔)입니다. 리포트 1회에 입력 약 3천 토큰·출력 약 5백 토큰이라 **한 달(30회) 약 $0.03 이하**입니다. 검증에 실패해 재생성해도 최대 2배입니다.

---

## 5. 문제가 생기면 어떻게 되나요?

| 상황 | 동작 |
|---|---|
| 매체 하나의 API가 죽음 | 나머지 매체로 리포트 발송, 메시지 하단에 "⚙️ 데이터 이슈" 표시, 실행 상태 `partial` |
| LLM 요약의 숫자가 JSON과 다름 | 1회 재생성 → 그래도 틀리면 LLM 없이 템플릿 리포트 발송 |
| API 키 없음 / LLM 장애 | 템플릿 리포트 발송 |
| Slack 발송 실패 | 이메일로 발송 (설정한 경우) |
| 전체 실패 | Slack에 🚨 실패 알림 + Actions 실패 표시 |
| 같은 날짜를 다시 실행 | 덮어쓰기(upsert) → 중복 행 없음 |

모든 실행 결과는 `run_logs` 테이블에 남습니다. Supabase SQL Editor에서:
```sql
SELECT report_date, status, detail->'steps'->'summary'->>'source' AS summary_by, finished_at
FROM run_logs ORDER BY finished_at DESC LIMIT 10;
```
```sql
SELECT a.report_date, a.severity, a.rule, c.name, a.value, a.baseline
FROM alerts a JOIN campaigns c USING (campaign_id) ORDER BY a.report_date DESC;
```

로컬에서 매체 장애를 재현하려면: `MOCK_FAIL_MEDIA=tiktok uvicorn mock_api.app:app --port 8000`

---

## 6. 설계에서 신경 쓴 점

- **숫자는 SQL, 문장은 LLM** — 합계·변화율·CPA·경보는 모두 SQL(`pipeline/metrics.py`)에서 계산합니다. LLM에는 `"4,820,000원"`, `"+12.4%"`처럼 표기가 끝난 문자열만 줍니다.
- **검증기** (`pipeline/validator.py`) — 요약문에서 `원`/`%`/`건`이 붙은 숫자를 모두 뽑아 JSON 값과 대조합니다. `482만원`처럼 단위를 바꿔 쓴 것도 값으로 환산해 비교하므로 "약 480만원" 같은 반올림도 잡아냅니다. 경보 캠페인이 요약에서 빠져도 실패로 봅니다.
- **멱등성** — `fact_daily`는 `(report_date, campaign_id)` upsert, `alerts`는 날짜 단위로 지우고 다시 씁니다. 몇 번을 다시 돌려도 결과가 같습니다.
- **Adapter 패턴** — 새 매체는 `pipeline/adapters/`에 파일 하나 추가 + `ADAPTERS`에 한 줄 등록이면 됩니다. 실제 매체 API로 바꿀 때도 `fetch_*`만 교체합니다.
- **시간대** — TikTok은 UTC 시간별 데이터를 받아 KST 하루(UTC 전날 15:00 ~ 당일 14:59)로 다시 합칩니다. 일별 UTC 합계로는 정확히 바꿀 수 없기 때문입니다.
- **보안** — 비밀값은 GitHub Secrets / `.env`에만. 모든 테이블에 RLS를 켜고 정책을 두지 않아 Supabase 공개 키(anon)로는 접근할 수 없습니다. LLM에는 집계 수치만 보내고 원본 응답은 보내지 않습니다. 개인 식별 정보는 다루지 않습니다(캠페인 단위 집계만).

### 심어 둔 이상 징후 (Mock 데이터)
| 규칙 | 캠페인 | 발생 주기 |
|---|---|---|
| `ctr_drop` | 신규설치_UA (TikTok) | 4일마다 |
| `overspend` | 겨울세일_리타겟팅 (Meta) | 3일마다 |
| `cpa_spike` | PMax_쇼핑 (Google) | 5일마다 |
| `missing_data` | 유튜브_리마케팅 (Google) | 6일마다 |

같은 날짜는 언제 요청해도 같은 값이 나오도록 날짜를 seed로 씁니다. `브랜드_영상조회(Meta)`는 일시정지 캠페인이라 데이터가 없어도 경보가 나지 않아야 합니다.

### 테스트
```bash
pytest -q
```
- Adapter 정규화 (micros 변환, 중첩 전환, TikTok UTC→KST 경계)
- 검증기 (지어낸 숫자·반올림·경보 누락 탐지)
- DB 통합: 재실행 시 중복 0건, 3주간 심어 둔 이상 징후를 **전부, 그리고 그것만** 탐지, 템플릿 리포트가 자체 검증 통과, LLM 재생성 → fallback

---

## 7. 폴더 구조
```
mock_api/            가짜 매체 API 3종 + 데이터 생성기
pipeline/
  adapters/          meta.py, google.py, tiktok.py (+ base.py 인터페이스)
  collector.py       매체별 수집 (한 매체 실패해도 계속)
  loader.py          upsert 적재
  metrics.py         SQL 집계 + 이상 탐지
  summarizer.py      DeepSeek 요약 / 템플릿 리포트
  validator.py       요약문 숫자 대조
  notifier.py        Slack → 이메일 → 콘솔
  __main__.py        실행 명령 (init-db / backfill / daily)
sql/schema.sql       테이블 · View · RLS
config.yaml          임계값, 매체, 환율, LLM 설정
.github/workflows/   daily.yml (매일 08:00 KST), test.yml
```

## 8. 다음 단계
- 실제 매체 API Adapter로 교체 (인터페이스는 이미 분리됨)
- Slack에서 "이 캠페인 7일 추이 보여줘" → SQL 조회로 답하는 봇 (Tool use / MCP)
- 알림에 👍/👎 피드백을 저장해 임계값 조정에 반영
- 매체 청구액과 내부 집계를 대조하는 정산 대사 자동화
