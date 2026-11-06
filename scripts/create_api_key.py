"""Create an account and API key.

Usage:
    python -m scripts.create_api_key --name "Acme" --email ops@acme.test
"""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import UTC, datetime, timedelta

from app.config import get_settings
from app.core.security import generate_api_key
from app.database.session import Database
from app.models.account import Account
from app.models.api_key import ApiKey
from app.models.application import Application
from sqlalchemy import select


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create an API key for the Universal SERP API")
    parser.add_argument("--name", required=True, help="Account name")
    parser.add_argument("--email", default=None, help="Account email (unique)")
    parser.add_argument("--plan", default="free", help="Plan label")
    parser.add_argument("--application", default=None, help="Application name to attach the key to")
    parser.add_argument("--key-name", default="default", help="Human readable API key name")
    parser.add_argument("--prefix", default="sk_live", help="Raw key prefix")
    parser.add_argument("--rate-limit", type=int, default=None, help="Requests per minute override")
    parser.add_argument("--daily-quota", type=int, default=None, help="Daily request quota override")
    parser.add_argument("--expires-days", type=int, default=None, help="Expiry in days")
    return parser.parse_args()


async def create_key(args: argparse.Namespace) -> dict[str, object]:
    settings = get_settings()
    db = Database(settings)
    try:
        async with db.session_factory() as session:
            account = None
            if args.email:
                account = (
                    await session.execute(select(Account).where(Account.email == args.email))
                ).scalar_one_or_none()
            if account is None:
                account = Account(
                    name=args.name,
                    email=args.email,
                    plan=args.plan,
                    daily_quota=args.daily_quota,
                    rate_limit_per_minute=args.rate_limit,
                )
                session.add(account)
                await session.flush()

            application = None
            if args.application:
                application = (
                    await session.execute(
                        select(Application).where(
                            Application.account_id == account.id,
                            Application.name == args.application,
                        )
                    )
                ).scalar_one_or_none()
                if application is None:
                    application = Application(account_id=account.id, name=args.application)
                    session.add(application)
                    await session.flush()

            raw_key, key_prefix, key_hash = generate_api_key(args.prefix)
            expires_at = datetime.now(UTC) + timedelta(days=args.expires_days) if args.expires_days else None
            api_key = ApiKey(
                account_id=account.id,
                application_id=application.id if application else None,
                name=args.key_name,
                key_hash=key_hash,
                key_prefix=key_prefix,
                expires_at=expires_at,
                rate_limit_per_minute=args.rate_limit,
                daily_quota=args.daily_quota,
            )
            session.add(api_key)
            await session.commit()
            return {
                "account_id": str(account.id),
                "application_id": str(application.id) if application else None,
                "api_key_id": str(api_key.id),
                "key_prefix": key_prefix,
                "api_key": raw_key,
                "expires_at": expires_at.isoformat() if expires_at else None,
                "notice": "Store this API key now; it cannot be retrieved again.",
            }
    finally:
        await db.dispose()


def main() -> None:
    print(json.dumps(asyncio.run(create_key(parse_args())), indent=2))


if __name__ == "__main__":
    main()
