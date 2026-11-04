import runpy
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch

from aiogram import Bot, Dispatcher
from aiogram.filters import CommandObject
from aiogram.types import Message, Update
from helpers import DatabaseCase
from test_registration_service import RESOLVED, URL
from test_status_middleware import message

from lovogit.app.providers.errors import (
    AuthenticationError,
    InvalidRepoURL,
    RateLimited,
    RepoNotFound,
    RevisionUnavailable,
    SourceUnavailable,
)
from lovogit.app.providers.github import GitHubProvider
from lovogit.app.services.registration import (
    RegisteredRepositoryDTO,
    RegistrationConflict,
    RegistrationOutcome,
    RegistrationUnavailable,
    RepositoryRegistrationService,
)
from lovogit.bot.add_repo import add_repo, create_registration_router
from lovogit.bot.attention import create_attention_router
from lovogit.bot.status import create_status_router
from lovogit.db import RepoModel


class RegistrationHandlerTests(DatabaseCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.provider = AsyncMock(spec=GitHubProvider)
        self.provider.resolve.return_value = RESOLVED
        self.service = RepositoryRegistrationService(self.provider, self.sessions)

    async def test_dispatcher_access_args_and_equal_rights(self):
        bot = Bot("123456:fake-token")
        self.addAsyncCleanup(bot.session.close)
        for allowed in ([], [-100]):
            dispatcher = Dispatcher()
            general = runpy.run_path(str(Path("src/lovogit/bot/commands.py")))["router"]
            sessions = AsyncMock()
            service = RepositoryRegistrationService(self.provider, sessions)
            dispatcher.include_routers(
                general,
                create_status_router([-100], self.sessions),
                create_attention_router([-100], self.sessions),
                create_registration_router(allowed, service),
            )
            with patch.object(Message, "answer", new_callable=AsyncMock) as answer:
                await dispatcher.feed_update(
                    bot,
                    Update(
                        update_id=1,
                        message=message(chat_id=-200).model_copy(
                            update={"text": "/add_repo " + URL}
                        ),
                    ),
                )
                answer.assert_awaited_once_with("Нет доступа", parse_mode=None)
                sessions.begin.assert_not_called()
                self.provider.resolve.assert_not_called()
                if not allowed:
                    answer.reset_mock()
                    await dispatcher.feed_update(
                        bot,
                        Update(
                            update_id=2,
                            message=message().model_copy(
                                update={"text": "/add_repo " + URL}
                            ),
                        ),
                    )
                    answer.assert_awaited_once_with("Нет доступа", parse_mode=None)
                    continue
                for text in (
                    "/add_repo",
                    "/add_repo " + URL + " extra",
                    "/add_repo ssh://github.com/team/project",
                ):
                    answer.reset_mock()
                    await dispatcher.feed_update(
                        bot,
                        Update(
                            update_id=3,
                            message=message().model_copy(update={"text": text}),
                        ),
                    )
                    self.assertEqual(answer.await_count, 1)
                    sessions.begin.assert_not_called()
                    self.provider.resolve.assert_not_called()
        dispatcher = Dispatcher()
        general = runpy.run_path(str(Path("src/lovogit/bot/commands.py")))["router"]
        dispatcher.include_routers(
            general,
            create_status_router([-100], self.sessions),
            create_attention_router([-100], self.sessions),
            create_registration_router([-100], self.service),
        )
        with patch.object(Message, "answer", new_callable=AsyncMock) as answer:
            for index, user_id in enumerate((1, 2)):
                await dispatcher.feed_update(
                    bot,
                    Update(
                        update_id=index + 10,
                        message=message(user_id=user_id).model_copy(
                            update={"text": "/add_repo " + URL}
                        ),
                    ),
                )
            texts = [call.args[0] for call in answer.await_args_list]
        self.assertIn("Репозиторий добавлен", texts[0])
        self.assertIn("Репозиторий уже отслеживается", texts[1])
        self.assertEqual(texts[0].splitlines()[1:], texts[1].splitlines()[1:])

    async def test_outcomes_and_plain_text(self):
        service = AsyncMock(spec=RepositoryRegistrationService)
        for outcome, label in (
            (RegistrationOutcome.ADDED, "Репозиторий добавлен"),
            (RegistrationOutcome.ALREADY_TRACKED, "Репозиторий уже отслеживается"),
            (RegistrationOutcome.RESTORED, "Отслеживание восстановлено"),
        ):
            service.add_repository.return_value = RegisteredRepositoryDTO(
                7, "<owner>_*repo", URL, outcome
            )
            with patch.object(Message, "answer", new_callable=AsyncMock) as answer:
                await add_repo(
                    message(),
                    CommandObject(prefix="/", command="add_repo", args=URL),
                    service,
                )
            answer.assert_awaited_once_with(
                f"{label}\n<owner>_*repo (ID: 7)\n{URL}\nАнализ не запущен",
                parse_mode=None,
            )

    async def test_safe_source_errors_rate_limit_conflict_and_storage_error(self):
        service = AsyncMock(spec=RepositoryRegistrationService)
        for error in (
            InvalidRepoURL(),
            AuthenticationError(),
            RepoNotFound(),
            RevisionUnavailable(),
            SourceUnavailable(),
            RateLimited(retry_at=datetime(2026, 11, 3, tzinfo=UTC)),
            RegistrationConflict("private detail"),
        ):
            service.add_repository.side_effect = error
            with patch.object(Message, "answer", new_callable=AsyncMock) as answer:
                await add_repo(
                    message(),
                    CommandObject(prefix="/", command="add_repo", args=URL),
                    service,
                )
            text = answer.await_args.args[0]
            self.assertNotIn("private detail", text)
            self.assertEqual(answer.await_args.kwargs, {"parse_mode": None})
            if isinstance(error, RateLimited):
                self.assertIn("03.11.2026 00:00:00 UTC", text)
            elif isinstance(error, RegistrationConflict):
                self.assertIn("администратору", text)
            else:
                self.assertEqual(text, error.user_message)
        error = RegistrationUnavailable(
            "Не удалось сохранить регистрацию GitHub ID 123"
        )
        error.__cause__ = RuntimeError("SQL token=private-secret")
        service.add_repository.side_effect = error
        with (
            patch.object(Message, "answer", new_callable=AsyncMock) as answer,
            self.assertLogs("lovogit.bot.add_repo") as logs,
        ):
            await add_repo(
                message(),
                CommandObject(prefix="/", command="add_repo", args=URL),
                service,
            )
        answer.assert_awaited_once_with(
            "Не удалось добавить репозиторий. Попробуйте позже", parse_mode=None
        )
        self.assertIn("RuntimeError", logs.output[0])
        self.assertNotIn("private-secret", logs.output[0])
        self.assertNotIn("SQL", answer.await_args.args[0])

    async def test_send_error_does_not_rollback_committed_project(self):
        with patch.object(Message, "answer", new_callable=AsyncMock) as answer:
            answer.side_effect = RuntimeError("Telegram offline")
            with self.assertRaisesRegex(RuntimeError, "Telegram offline"):
                await add_repo(
                    message(),
                    CommandObject(prefix="/", command="add_repo", args=URL),
                    self.service,
                )
        repeated = await self.service.add_repository(URL)
        self.assertEqual(repeated.outcome, RegistrationOutcome.ALREADY_TRACKED)
        async with self.sessions() as session:
            self.assertEqual(
                (await session.get(RepoModel, repeated.project_id)).github_id, 123
            )
