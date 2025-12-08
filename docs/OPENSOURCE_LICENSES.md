# Pixell Agent Runtime - 오픈소스 라이선스

## 프로덕션 의존성 (Production Dependencies)

| 오픈소스 이름 | 활용 내용 | 라이선스 | 링크 |
|--------------|----------|---------|------|
| FastAPI | 비동기 웹 프레임워크, Supervisor API 서버(port 9000) 구현, REST 엔드포인트 제공 | MIT | https://github.com/tiangolo/fastapi |
| Uvicorn | ASGI 서버, FastAPI 애플리케이션 실행, HTTP/2 및 WebSocket 지원 | BSD-3-Clause | https://github.com/encode/uvicorn |
| Pydantic | 데이터 검증 및 직렬화, 요청/응답 모델 정의 (DeployRequest, AgentManifest 등) | MIT | https://github.com/pydantic/pydantic |
| Pydantic-Settings | 환경 변수 기반 설정 관리, 애플리케이션 구성 로딩 | MIT | https://github.com/pydantic/pydantic-settings |
| HTTPX | 비동기 HTTP 클라이언트, HTTPS URL에서 apkg 파일 다운로드 | BSD-3-Clause | https://github.com/encode/httpx |
| Boto3 | AWS SDK, S3에서 apkg 다운로드, CodeArtifact 인증, EC2/ECS 작업 | Apache-2.0 | https://github.com/boto/boto3 |
| PyYAML | YAML 파싱, agent.yaml 매니페스트 파일 읽기 및 배포 구성 파싱 | MIT | https://github.com/yaml/pyyaml |
| Prometheus-Client | 메트릭 수집 및 노출, 배포 카운트/요청 지연시간/에이전트 상태 모니터링 | Apache-2.0 | https://github.com/prometheus/client_python |
| Python-Jose | JWT 토큰 생성/검증, 보안 인증 처리 | MIT | https://github.com/mpdavis/python-jose |
| Python-Multipart | multipart/form-data 파싱, FastAPI 파일 업로드 지원 | Apache-2.0 | https://github.com/andrew-d/python-multipart |
| Structlog | 구조화된 로깅, 컨텍스트 포함 일관된 로그 출력 | MIT / Apache-2.0 | https://github.com/hynek/structlog |
| AIOFiles | 비동기 파일 I/O, 로그 및 패키지 추출 시 논블로킹 작업 | Apache-2.0 | https://github.com/Tinche/aiofiles |
| Cryptography | 암호화 프리미티브, TLS/SSL 작업, SHA256 해싱, 보안 키 생성 | Apache-2.0 / BSD-3-Clause | https://github.com/pyca/cryptography |
| gRPC | 고성능 RPC 프레임워크, A2A(Agent-to-Agent) 통신, GrpcGateway 구현 | Apache-2.0 | https://github.com/grpc/grpc |
| gRPC-Tools | Protobuf 컴파일러, .proto 파일에서 Python 코드 생성 (agent_pb2.py) | Apache-2.0 | https://github.com/grpc/grpc |
| Protobuf | Protocol Buffers 직렬화, A2A 메시지 직렬화 (A2AMessage, ActionResult) | BSD-3-Clause | https://github.com/protocolbuffers/protobuf |
| PSUtil | 프로세스 및 시스템 유틸리티, 에이전트 프로세스 관리, 리소스 모니터링, 좀비 프로세스 감지 | BSD-3-Clause | https://github.com/giampaolo/psutil |
| Click | CLI 도구 생성, pixell-runtime 명령어 인터페이스, 인자 파싱 | BSD-3-Clause | https://github.com/pallets/click |

## 개발 의존성 (Development Dependencies)

| 오픈소스 이름 | 활용 내용 | 라이선스 | 링크 |
|--------------|----------|---------|------|
| Pytest | 테스트 프레임워크, 유닛 테스트 및 통합 테스트 실행 | MIT | https://github.com/pytest-dev/pytest |
| Pytest-Asyncio | Pytest 비동기 플러그인, async 함수 테스트, 이벤트 루프 관리 | Apache-2.0 | https://github.com/pytest-dev/pytest-asyncio |
| Pytest-Cov | 코드 커버리지 측정, 테스트 완전성 보장 | MIT | https://github.com/pytest-dev/pytest-cov |
| Moto | AWS 서비스 모킹, S3 작업 모킹하여 실제 AWS 없이 테스트 | Apache-2.0 | https://github.com/getmoto/moto |
| MyPy | 정적 타입 체커, 런타임 전 타입 오류 감지 | MIT | https://github.com/python/mypy |
| Ruff | 고속 Python 린터 (Rust 기반), 코드 품질 검사 (pycodestyle, pyflakes, isort 등) | MIT | https://github.com/astral-sh/ruff |
| Black | 코드 포매터, 일관된 코드 스타일 유지 | MIT | https://github.com/psf/black |
| Isort | Import 문 정렬, Python import 구문 정리 및 정렬 | MIT | https://github.com/PyCQA/isort |
| Pre-Commit | Git 훅 관리자, 커밋 전 자동으로 린터/포매터 실행 | MIT | https://github.com/pre-commit/pre-commit |
| Types-PyYAML | PyYAML 타입 스텁, MyPy 타입 체킹 지원 | Apache-2.0 | https://github.com/python/typeshed |
| Types-AIOFiles | AIOFiles 타입 스텁, MyPy 타입 체킹 지원 | Apache-2.0 | https://github.com/python/typeshed |
| Boto3-Stubs | Boto3 타입 스텁, Boto3 S3 작업에 대한 MyPy 타입 체킹 지원 | MIT | https://github.com/youtype/mypy_boto3_builder |

## 라이선스 요약

| 라이선스 | 패키지 수 | 패키지 목록 |
|---------|----------|------------|
| MIT | 15 | FastAPI, Pydantic, Pydantic-Settings, PyYAML, Python-Jose, Structlog, Pytest, Pytest-Cov, MyPy, Ruff, Black, Isort, Pre-Commit, Boto3-Stubs |
| Apache-2.0 | 10 | Boto3, Prometheus-Client, Python-Multipart, AIOFiles, gRPC, gRPC-Tools, Pytest-Asyncio, Moto, Types-PyYAML, Types-AIOFiles |
| BSD-3-Clause | 5 | Uvicorn, HTTPX, Protobuf, PSUtil, Click |
| Apache-2.0 / BSD-3-Clause | 1 | Cryptography |
| MIT / Apache-2.0 | 1 | Structlog |

**총 31개 오픈소스 패키지 사용** (프로덕션 18개 + 개발 13개)

모든 패키지는 상업적 사용이 허용되는 허용적 오픈소스 라이선스를 사용합니다.

## 주요 용도별 분류

### 웹 프레임워크 & API (5개)
- FastAPI, Uvicorn, Pydantic, Pydantic-Settings, Python-Multipart

### 네트워크 & 통신 (5개)
- HTTPX, gRPC, gRPC-Tools, Protobuf, Boto3

### 데이터 & 구성 (2개)
- PyYAML, AIOFiles

### 모니터링 & 로깅 (2개)
- Prometheus-Client, Structlog

### 보안 & 인증 (2개)
- Python-Jose, Cryptography

### 시스템 유틸리티 (2개)
- PSUtil, Click

### 테스트 (4개)
- Pytest, Pytest-Asyncio, Pytest-Cov, Moto

### 코드 품질 (5개)
- MyPy, Ruff, Black, Isort, Pre-Commit

### 타입 스텁 (3개)
- Types-PyYAML, Types-AIOFiles, Boto3-Stubs

---

생성일: 2025-11-04
프로젝트: Pixell Agent Runtime v0.2.1
