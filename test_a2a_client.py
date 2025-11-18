#!/usr/bin/env python3
"""
HTTP 기반 A2A/REST 헬스 체크 클라이언트.

기존 gRPC A2A 클라이언트를 버리고, 현재 구조(HTTP A2A + REST)를 기준으로
간단한 헬스 체크를 수행하는 스크립트입니다.

기본 사용:
    python test_a2a_client.py               # http://127.0.0.1:50051 기준
    python test_a2a_client.py 127.0.0.1:9999
    python test_a2a_client.py http://127.0.0.1:9999
"""

import asyncio
import sys
from urllib.parse import urlparse

import httpx


async def test_http_a2a(base_url: str) -> None:
    """HTTP A2A / REST 헬스 체크를 수행한다."""
    # base_url 정규화
    if not base_url.startswith("http://") and not base_url.startswith("https://"):
        base_url = f"http://{base_url}"

    parsed = urlparse(base_url)
    host_port = f"{parsed.hostname}:{parsed.port or (443 if parsed.scheme == 'https' else 80)}"

    print(f"🤖 HTTP 기반 A2A/REST 헬스 체크")
    print(f"📍 Target: {base_url} ({host_port})")
    print("=" * 60)

    async with httpx.AsyncClient(timeout=5.0) as client:
        # 1. /health (REST 런타임 또는 앱 헬스)
        print("\n1️⃣ GET /health")
        try:
            resp = await client.get(f"{base_url}/health")
            print(f"✅ 상태코드: {resp.status_code}")
            try:
                print(f"✅ 응답 JSON: {resp.json()}")
            except Exception:
                print(f"ℹ️ 응답 텍스트: {resp.text[:500]}")
        except Exception as e:
            print(f"❌ /health 요청 실패: {e}")

        # 2. / (루트 페이지 - UI 혹은 기본 응답)
        print("\n2️⃣ GET /")
        try:
            resp = await client.get(f"{base_url}/")
            print(f"✅ 상태코드: {resp.status_code}")
            text = resp.text
            print(f"ℹ️ 본문 미리보기:\n{text[:500]}")
        except Exception as e:
            print(f"❌ / 요청 실패: {e}")

        # 3. 선택: /meta (Pixell Runtime REST가 노출하는 메타 정보, 없으면 그냥 스킵)
        print("\n3️⃣ GET /meta (있으면 Pixell Runtime 메타 정보)")
        try:
            resp = await client.get(f"{base_url}/meta")
            if resp.status_code == 200:
                print(f"✅ 상태코드: {resp.status_code}")
                try:
                    print(f"✅ 응답 JSON: {resp.json()}")
                except Exception:
                    print(f"ℹ️ 응답 텍스트: {resp.text[:500]}")
            else:
                print(f"ℹ️ /meta 상태코드: {resp.status_code} (엔드포인트 없을 수 있음)")
        except Exception as e:
            print(f"ℹ️ /meta 요청 실패 (엔드포인트 없을 수 있음): {e}")


def main() -> None:
    """엔트리 포인트."""
    if len(sys.argv) > 1:
        target = sys.argv[1]
    else:
        # 기본값: HTTP A2A를 50051 포트로 띄웠다고 가정
        target = "127.0.0.1:50051"

    asyncio.run(test_http_a2a(target))


if __name__ == "__main__":
    main()