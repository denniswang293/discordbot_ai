from __future__ import annotations

import asyncio
import copy
import datetime as dt
import io
import json
import logging
import math
import os
import random
from pathlib import Path
from typing import Any

import discord
from discord.ext import commands, tasks
from PIL import Image, ImageDraw, ImageFont, ImageOps, UnidentifiedImageError


logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
JSON_DIR = PROJECT_ROOT / "data" / "json"
ECONOMY_CONFIG_FILE = JSON_DIR / "economy.json"
PLAYER_FILE = JSON_DIR / "player_data.json"
MARKET_FILE = JSON_DIR / "market.json"
LOG_FILE = JSON_DIR / "economy_log.json"
BATTLE_IMAGE = PROJECT_ROOT / "ccpt" / "assets" / "economy" / "battle.png"
BATTLE_FONT = PROJECT_ROOT / "ccpt" / "assets" / "economy" / "ch.ttf"
BATTLE_FONT_CANDIDATES = (
    BATTLE_FONT,
    Path("C:/Windows/Fonts/msjh.ttc"),
    Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
    Path("/usr/share/fonts/opentype/noto/NotoSansTC-Regular.otf"),
    Path("/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"),
)

EMBED_COLOUR = 0x00FFE1
START_WALLET = 1000
MAX_LOG_ENTRIES = 100
CARD_TYPES = ("N", "R", "SR", "UR", "SSR")

DEFENSE_POWER_BONUS = 1.10
DEFAULT_DEFENSE_RATIO = 0.30
WINNER_CASUALTY_RANGE = (0.10, 0.25)
LOSER_CASUALTY_RANGE = (0.30, 0.50)
SAFE_BANK = 5000
LOOT_RATE_RANGE = (0.05, 0.10)
LOOT_PER_POWER = 15
DEFENSE_SUCCESS_PROTECTION = 30 * 60
DEFENSE_DEFEAT_PROTECTION = 60 * 60

CARD_NAMES = {
    "N": "N:士兵",
    "R": "R:戰鬥機",
    "SR": "SR:坦克",
    "UR": "UR:航母",
    "SSR": "SSR:核彈",
}
CARD_POWER = {"N": 1, "R": 2, "SR": 5, "UR": 8, "SSR": 10}
CARD_ALIASES = {
    "n": "N",
    "n:士兵": "N",
    "士兵": "N",
    "r": "R",
    "r:戰鬥機": "R",
    "r:战斗机": "R",
    "戰鬥機": "R",
    "战斗机": "R",
    "sr": "SR",
    "sr:坦克": "SR",
    "坦克": "SR",
    "ur": "UR",
    "ur:航母": "UR",
    "航母": "UR",
    "ssr": "SSR",
    "ssr:核彈": "SSR",
    "ssr:核弹": "SSR",
    "核彈": "SSR",
    "核弹": "SSR",
}
DEFAULT_MARKET = {
    "random": 192,
    "N": 131,
    "R": 170,
    "SR": 409,
    "UR": 660,
    "SSR": 838,
}
MARKET_RULES = {
    "random": (150, 200, 15),
    "N": (60, 150, 30),
    "R": (150, 270, 25),
    "SR": (340, 480, 20),
    "UR": (530, 690, 15),
    "SSR": (720, 900, 10),
}
START_ATTACK = {"N": 3, "R": 3, "SR": 1, "UR": 0, "SSR": 0}

class EconomyDataError(RuntimeError):
    """Raised when an economy JSON file exists but cannot be trusted."""


class FightConfirmationView(discord.ui.View):
    """A confirmation view that only the command author can operate."""

    def __init__(self, author_id: int, *, timeout: float = 30):
        super().__init__(timeout=timeout)
        self.author_id = author_id
        self.confirmed: bool | None = None
        self.message: discord.Message | None = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.author_id:
            return True
        await interaction.response.send_message("只有發起攻擊的玩家可以操作。", ephemeral=True)
        return False

    def disable_buttons(self) -> None:
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                child.disabled = True

    async def finish(self, interaction: discord.Interaction, confirmed: bool) -> None:
        self.confirmed = confirmed
        self.disable_buttons()
        await interaction.response.edit_message(view=self)
        self.stop()

    @discord.ui.button(label="確認攻擊", style=discord.ButtonStyle.danger, emoji="⚔️")
    async def confirm_button(
        self,
        interaction: discord.Interaction,
        _button: discord.ui.Button,
    ) -> None:
        await self.finish(interaction, True)

    @discord.ui.button(label="取消", style=discord.ButtonStyle.secondary)
    async def cancel_button(
        self,
        interaction: discord.Interaction,
        _button: discord.ui.Button,
    ) -> None:
        await self.finish(interaction, False)

    async def on_timeout(self) -> None:
        self.disable_buttons()
        if self.message is not None:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass


def normalize_card(value: str | None) -> str | None:
    if value is None:
        return None
    return CARD_ALIASES.get(value.strip().lower())


def parse_amount(value: str, *, maximum: int | None = None) -> int:
    """Parse a positive amount, with case-insensitive ``all`` support."""
    if value.strip().lower() == "all":
        if maximum is None:
            raise ValueError("這個操作不支援 all")
        amount = maximum
    else:
        try:
            amount = int(value)
        except (TypeError, ValueError) as error:
            raise ValueError("數量必須是正整數或 all") from error

    if amount <= 0:
        raise ValueError("數量必須大於 0")
    if maximum is not None and amount > maximum:
        raise ValueError("持有數量不足")
    return amount


def parse_non_negative_amount(value: str) -> int:
    """Parse battle deployment values, where zero means do not deploy."""
    try:
        amount = int(value)
    except (TypeError, ValueError) as error:
        raise ValueError("部署數量必須是非負整數") from error
    if amount < 0:
        raise ValueError("部署數量不能小於 0")
    return amount


def parse_defense_ratio(value: str) -> float:
    """Accept either a decimal ratio (0.3) or a percentage (30)."""
    try:
        ratio = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError("自動防禦比例格式錯誤；例如輸入 0.3 或 30") from error
    if ratio > 1:
        ratio /= 100
    if not 0 < ratio <= 1:
        raise ValueError("自動防禦比例必須大於 0%，且不能超過 100%")
    return ratio


def random_fraction(amount: int, low: float = 1 / 9, high: float = 1 / 3) -> int:
    if amount <= 0:
        return 0
    minimum = max(1, int(amount * low))
    maximum = max(minimum, int(amount * high))
    return random.randint(minimum, maximum)


def weighted_choice(options: list[dict[str, Any]]) -> dict[str, Any]:
    """Choose one configured option by its non-negative ``weight`` value."""
    if not options:
        raise EconomyDataError("加權選項不可為空")
    weights = [float(option.get("weight", 0)) for option in options]
    if any(weight < 0 for weight in weights) or not any(weights):
        raise EconomyDataError("加權選項必須至少有一個正權重")
    return random.choices(options, weights=weights, k=1)[0]


class Economy(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.data_lock = asyncio.Lock()

        if not ECONOMY_CONFIG_FILE.exists():
            raise EconomyDataError(f"找不到 Economy 設定檔：{ECONOMY_CONFIG_FILE}")
        loaded_config = self._load_json(ECONOMY_CONFIG_FILE, {})
        self.config = self._validate_config(loaded_config)

        self.players: dict[str, dict[str, Any]] = self._load_json(PLAYER_FILE, {})
        loaded_market = self._load_json(MARKET_FILE, DEFAULT_MARKET)
        self.market = self._validate_market(loaded_market)
        self.logs: dict[str, list[dict[str, Any]]] = self._load_json(LOG_FILE, {})

        players_upgraded = False
        for user_id, player in self.players.items():
            if not isinstance(player, dict):
                raise EconomyDataError(f"玩家 {user_id} 的資料格式錯誤")
            players_upgraded = self._upgrade_player_battle_data(player) or players_upgraded

        # Persist defaults only when files do not exist. Existing malformed JSON
        # raises above and is never silently replaced with an empty database.
        self._ensure_data_files()
        if players_upgraded:
            self._write_json_atomic(PLAYER_FILE, self.players)

    async def cog_load(self) -> None:
        self.market_loop.start()
        self.bank_interest_loop.start()

    def cog_unload(self) -> None:
        self.market_loop.cancel()
        self.bank_interest_loop.cancel()

    # ----------------------------- JSON storage -----------------------------

    @staticmethod
    def _load_json(path: Path, default: dict[str, Any]) -> dict[str, Any]:
        if not path.exists():
            return copy.deepcopy(default)
        try:
            with path.open("r", encoding="utf-8") as file:
                data = json.load(file)
        except json.JSONDecodeError as error:
            raise EconomyDataError(f"JSON 資料損壞，拒絕覆蓋：{path}") from error
        except OSError as error:
            raise EconomyDataError(f"無法讀取 Economy 資料：{path}") from error
        if not isinstance(data, dict):
            raise EconomyDataError(f"Economy JSON 最外層必須是物件：{path}")
        return data

    @staticmethod
    def _write_json_atomic(path: Path, data: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(f"{path.suffix}.tmp")
        try:
            with temporary.open("w", encoding="utf-8", newline="\n") as file:
                json.dump(data, file, ensure_ascii=False, indent=2)
                file.write("\n")
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary, path)
        finally:
            if temporary.exists():
                try:
                    temporary.unlink()
                except OSError:
                    logger.warning("無法清除暫存檔 %s", temporary)

    @staticmethod
    def _validate_market(data: dict[str, Any]) -> dict[str, int]:
        market = copy.deepcopy(DEFAULT_MARKET)
        for item, value in data.items():
            if item not in DEFAULT_MARKET:
                continue
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise EconomyDataError(f"market.json 的 {item} 價格必須是正整數")
            market[item] = value
        return market

    @staticmethod
    def _validate_config(data: dict[str, Any]) -> dict[str, Any]:
        """Validate the static economy config once when the Cog is loaded."""
        config = copy.deepcopy(data)
        general = data.get("general", {})
        if not isinstance(general, dict):
            raise EconomyDataError("economy.json 的 general 必須是物件")
        config["general"] = copy.deepcopy(general)

        fishing = data.get("fishing")
        if not isinstance(fishing, dict):
            raise EconomyDataError("economy.json 的 fishing 必須是物件")

        cooldown = fishing.get("cooldown")
        if isinstance(cooldown, bool) or not isinstance(cooldown, (int, float)) or cooldown <= 0:
            raise EconomyDataError("economy.json 的 fishing.cooldown 必須是正數")

        def validate_options(
            key: str,
            required: tuple[str, ...],
        ) -> list[dict[str, Any]]:
            options = fishing.get(key)
            if not isinstance(options, list) or not options:
                raise EconomyDataError(f"economy.json 的 fishing.{key} 必須是非空陣列")
            validated: list[dict[str, Any]] = []
            ids: set[str] = set()
            for option in options:
                if not isinstance(option, dict) or any(field not in option for field in required):
                    raise EconomyDataError(f"economy.json 的 fishing.{key} 選項格式錯誤")
                item = copy.deepcopy(option)
                option_id = item.get("id")
                weight = item.get("weight")
                if not isinstance(option_id, str) or not option_id.strip() or option_id in ids:
                    raise EconomyDataError(f"economy.json 的 fishing.{key} id 必須唯一且為文字")
                if isinstance(weight, bool) or not isinstance(weight, (int, float)) or weight < 0:
                    raise EconomyDataError(f"economy.json 的 fishing.{key} weight 必須是非負數")
                ids.add(option_id)
                validated.append(item)
            if not any(float(option["weight"]) > 0 for option in validated):
                raise EconomyDataError(f"economy.json 的 fishing.{key} 至少需要一個正權重")
            return validated

        rarities = validate_options("rarities", ("id", "name", "weight", "price_multiplier"))
        for rarity in rarities:
            multiplier = rarity["price_multiplier"]
            if isinstance(multiplier, bool) or not isinstance(multiplier, (int, float)) or multiplier <= 0:
                raise EconomyDataError("economy.json 的稀有度 price_multiplier 必須是正數")

        items = validate_options("items", ("id", "name", "category", "base_price", "weight"))
        for item in items:
            price = item["base_price"]
            if isinstance(price, bool) or not isinstance(price, (int, float)) or price < 0:
                raise EconomyDataError("economy.json 的物品 base_price 必須是非負數")

        config["fishing"] = {"cooldown": cooldown, "rarities": rarities, "items": items}
        if isinstance(data.get("general"), dict):
            config["general"].update(copy.deepcopy(data["general"]))

        work = data.get("work")
        if not isinstance(work, dict) or not isinstance(work.get("messages"), list) or not work["messages"]:
            raise EconomyDataError("economy.json 的 work.messages 必須是非空陣列")
        if not all(isinstance(message, str) and message.strip() for message in work["messages"]):
            raise EconomyDataError("economy.json 的 work.messages 必須全部是非空文字")
        config["work"] = {
            "cooldown": work.get("cooldown", 6),
            "messages": copy.deepcopy(work["messages"]),
        }
        return config

    def _ensure_data_files(self) -> None:
        JSON_DIR.mkdir(parents=True, exist_ok=True)
        for path, data in (
            (PLAYER_FILE, self.players),
            (MARKET_FILE, self.market),
            (LOG_FILE, self.logs),
        ):
            if not path.exists():
                self._write_json_atomic(path, data)

    async def _save_players(self) -> None:
        await asyncio.to_thread(self._write_json_atomic, PLAYER_FILE, self.players)

    async def _save_market(self) -> None:
        await asyncio.to_thread(self._write_json_atomic, MARKET_FILE, self.market)

    async def _save_logs(self) -> None:
        await asyncio.to_thread(self._write_json_atomic, LOG_FILE, self.logs)

    # ------------------------------- helpers --------------------------------

    @staticmethod
    def create_player_data() -> dict[str, Any]:
        return {
            "wallet": START_WALLET,
            "bank": 0,
            "cards": {card: 0 for card in CARD_TYPES},
            "battle": {
                "attack": copy.deepcopy(START_ATTACK),
                "defense": {
                    "mode": "auto",
                    "ratio": DEFAULT_DEFENSE_RATIO,
                    "troops": copy.deepcopy(START_ATTACK),
                },
                "protection_until": 0,
            },
        }

    @staticmethod
    def _upgrade_player_battle_data(player: dict[str, Any]) -> bool:
        """Upgrade the first Economy schema to Fight v2 without losing assets."""
        changed = False
        cards = player.get("cards")
        if not isinstance(cards, dict):
            raise EconomyDataError("玩家 cards 資料格式錯誤")
        for card in CARD_TYPES:
            if card not in cards:
                cards[card] = 0
                changed = True

        battle = player.get("battle")
        if not isinstance(battle, dict):
            legacy = player.get("settings", {})
            legacy_attack = legacy.get("attack", START_ATTACK) if isinstance(legacy, dict) else START_ATTACK
            legacy_defense = legacy.get("defense", START_ATTACK) if isinstance(legacy, dict) else START_ATTACK
            manual_defense = isinstance(legacy, dict) and not legacy.get("auto_battle", True)
            battle = {
                "attack": {
                    card: max(0, int(legacy_attack.get(card, START_ATTACK[card])))
                    for card in CARD_TYPES
                },
                "defense": {
                    "mode": "manual" if manual_defense else "auto",
                    "ratio": DEFAULT_DEFENSE_RATIO,
                    "troops": {
                        card: max(0, int(legacy_defense.get(card, START_ATTACK[card])))
                        for card in CARD_TYPES
                    },
                },
                "protection_until": 0,
            }
            player["battle"] = battle
            changed = True

        attack = battle.get("attack")
        if not isinstance(attack, dict):
            attack = copy.deepcopy(START_ATTACK)
            battle["attack"] = attack
            changed = True
        defense = battle.get("defense")
        if not isinstance(defense, dict):
            defense = {}
            battle["defense"] = defense
            changed = True
        for card in CARD_TYPES:
            if card not in attack:
                attack[card] = START_ATTACK[card]
                changed = True
        if defense.get("mode") not in {"auto", "manual"}:
            defense["mode"] = "auto"
            changed = True
        ratio = defense.get("ratio")
        if not isinstance(ratio, (int, float)) or isinstance(ratio, bool) or not 0 < ratio <= 1:
            defense["ratio"] = DEFAULT_DEFENSE_RATIO
            changed = True
        troops = defense.get("troops")
        if not isinstance(troops, dict):
            troops = {}
            defense["troops"] = troops
            changed = True
        for card in CARD_TYPES:
            if card not in troops:
                troops[card] = START_ATTACK[card]
                changed = True
        if not isinstance(battle.get("protection_until"), (int, float)):
            battle["protection_until"] = 0
            changed = True
        if "settings" in player:
            del player["settings"]
            changed = True
        return changed

    def player_exists(self, user_id: int | str) -> bool:
        return str(user_id) in self.players

    def get_player(self, user_id: int | str) -> dict[str, Any] | None:
        return self.players.get(str(user_id))

    @staticmethod
    def calculate_army_power(army: dict[str, int]) -> int:
        return sum(army.get(card, 0) * CARD_POWER[card] for card in CARD_TYPES)

    @staticmethod
    def get_attack_army(player: dict[str, Any]) -> dict[str, int]:
        requested = player["battle"]["attack"]
        return {
            card: min(max(0, int(requested.get(card, 0))), player["cards"].get(card, 0))
            for card in CARD_TYPES
        }

    @staticmethod
    def get_defense_army(player: dict[str, Any]) -> dict[str, int]:
        defense = player["battle"]["defense"]
        if defense["mode"] == "manual":
            requested = defense["troops"]
            return {
                card: min(max(0, int(requested.get(card, 0))), player["cards"].get(card, 0))
                for card in CARD_TYPES
            }
        ratio = float(defense["ratio"])
        return {
            card: min(
                player["cards"].get(card, 0),
                math.floor(player["cards"].get(card, 0) * ratio + 0.5),
            )
            for card in CARD_TYPES
        }

    @staticmethod
    def calculate_win_rate(attacker_power: int, defender_power: int) -> float:
        if attacker_power <= 0:
            return 0.0
        if defender_power <= 0:
            return 1.0
        effective_defense = defender_power * DEFENSE_POWER_BONUS
        return attacker_power / (attacker_power + effective_defense)

    @staticmethod
    def describe_advantage(win_rate: float) -> str:
        if win_rate < 0.35:
            return "處於劣勢"
        if win_rate < 0.45:
            return "略處劣勢"
        if win_rate < 0.55:
            return "勢均力敵"
        if win_rate < 0.70:
            return "略有優勢"
        if win_rate < 0.85:
            return "明顯優勢"
        return "壓倒性優勢"

    @staticmethod
    def calculate_casualties(army: dict[str, int], won: bool) -> tuple[dict[str, int], float]:
        low, high = WINNER_CASUALTY_RANGE if won else LOSER_CASUALTY_RANGE
        casualty_rate = random.uniform(low, high)
        losses = {
            card: min(amount, math.floor(amount * casualty_rate + 0.5))
            for card, amount in army.items()
        }
        return losses, casualty_rate

    @staticmethod
    def get_loot_modifier(attacker_power: int, defender_power: int) -> float:
        if defender_power <= 0:
            return 0.10
        power_ratio = attacker_power / defender_power
        if power_ratio <= 1.5:
            return 1.0
        if power_ratio <= 2.5:
            return 0.70
        if power_ratio <= 4:
            return 0.40
        if power_ratio <= 10:
            return 0.20
        return 0.10

    @classmethod
    def calculate_loot(cls, target_bank: int, attacker_power: int, defender_power: int) -> int:
        stealable = max(0, target_bank - SAFE_BANK)
        if stealable <= 0:
            return 0
        loot_rate = random.uniform(*LOOT_RATE_RANGE)
        base_loot = min(stealable * loot_rate, attacker_power * LOOT_PER_POWER)
        return max(0, int(base_loot * cls.get_loot_modifier(attacker_power, defender_power)))

    @staticmethod
    def protection_remaining(player: dict[str, Any], now: float | None = None) -> int:
        current_time = dt.datetime.now(dt.timezone.utc).timestamp() if now is None else now
        protection_until = float(player["battle"].get("protection_until", 0))
        return max(0, math.ceil(protection_until - current_time))

    @staticmethod
    def _append_log(user_id: int | str, logs: dict[str, list[dict[str, Any]]], kind: str, **data: Any) -> None:
        key = str(user_id)
        entry = {
            "type": kind,
            **data,
            "time": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        }
        logs.setdefault(key, []).append(entry)
        logs[key] = logs[key][-MAX_LOG_ENTRIES:]

    @staticmethod
    def _reset_cooldown(ctx: commands.Context) -> None:
        if ctx.command is not None:
            ctx.command.reset_cooldown(ctx)

    @staticmethod
    def _footer_icon(member: discord.abc.User) -> str:
        return member.display_avatar.url

    @staticmethod
    def _render_battle_image(
        attacker_avatar: bytes,
        defender_avatar: bytes,
        attacker_name: str,
        defender_name: str,
        attacker_power: int,
        defender_power: int,
    ) -> io.BytesIO:
        with Image.open(BATTLE_IMAGE) as source:
            battle = source.convert("RGBA")

        def prepare_avatar(data: bytes) -> Image.Image:
            with Image.open(io.BytesIO(data)) as avatar_source:
                avatar = ImageOps.fit(
                    avatar_source.convert("RGBA"),
                    (100, 100),
                    method=Image.Resampling.LANCZOS,
                )
            mask = Image.new("L", (100, 100), 0)
            ImageDraw.Draw(mask).ellipse((0, 0, 99, 99), fill=255)
            avatar.putalpha(mask)
            return avatar

        attacker_image = prepare_avatar(attacker_avatar)
        defender_image = prepare_avatar(defender_avatar)
        battle.alpha_composite(attacker_image, (56, 45))
        battle.alpha_composite(defender_image, (309, 45))

        font = None
        for font_path in BATTLE_FONT_CANDIDATES:
            try:
                font = ImageFont.truetype(str(font_path), 18)
                break
            except OSError:
                continue
        if font is None:
            font = ImageFont.load_default(size=18)
        draw = ImageDraw.Draw(battle)
        draw.multiline_text(
            (55, 164),
            f"{attacker_name[:12]}\nPOWER: {attacker_power:,}",
            font=font,
            fill=(0, 0, 0),
            spacing=3,
        )
        draw.multiline_text(
            (308, 164),
            f"{defender_name[:12]}\nPOWER: {defender_power:,}",
            font=font,
            fill=(0, 0, 0),
            spacing=3,
        )

        buffer = io.BytesIO()
        battle.save(buffer, format="PNG")
        buffer.seek(0)
        return buffer

    async def build_battle_image(
        self,
        attacker: discord.Member,
        defender: discord.Member,
        attacker_power: int,
        defender_power: int,
    ) -> io.BytesIO:
        """Download display avatars and composite the battle card in memory."""
        try:
            attacker_avatar, defender_avatar = await asyncio.gather(
                attacker.display_avatar.replace(size=256, format="png").read(),
                defender.display_avatar.replace(size=256, format="png").read(),
            )
            return await asyncio.to_thread(
                self._render_battle_image,
                attacker_avatar,
                defender_avatar,
                attacker.display_name,
                defender.display_name,
                attacker_power,
                defender_power,
            )
        except (discord.HTTPException, OSError, UnidentifiedImageError, ValueError):
            logger.exception("無法產生 Fight 戰鬥圖片，改用原始模板")
            return io.BytesIO(BATTLE_IMAGE.read_bytes())

    async def _require_player(self, ctx: commands.Context) -> bool:
        if self.player_exists(ctx.author.id):
            return True
        await ctx.send(f"請先使用 `{ctx.clean_prefix}start` 建立帳號。")
        return False

    async def _confirm(
        self,
        ctx: commands.Context,
        message: discord.Message,
        *,
        timeout: float = 20,
    ) -> bool | None:
        try:
            await message.add_reaction("✅")
            await message.add_reaction("❌")
        except discord.HTTPException:
            await ctx.send("無法加入確認反應，請檢查機器人的反應權限。")
            return False

        def check(reaction: discord.Reaction, user: discord.abc.User) -> bool:
            return (
                user.id == ctx.author.id
                and reaction.message.id == message.id
                and str(reaction.emoji) in {"✅", "❌"}
            )

        try:
            reaction, _ = await self.bot.wait_for("reaction_add", timeout=timeout, check=check)
        except asyncio.TimeoutError:
            return None
        return str(reaction.emoji) == "✅"

    @staticmethod
    def _transaction_embed(title: str = "交易現場") -> discord.Embed:
        return discord.Embed(title=title, colour=EMBED_COLOUR, timestamp=discord.utils.utcnow())

    # --------------------------- background tasks ---------------------------

    @tasks.loop(hours=1)
    async def market_loop(self) -> None:
        async with self.data_lock:
            for item, (minimum, maximum, step) in MARKET_RULES.items():
                price = self.market[item]
                if price >= maximum:
                    price -= random.randint(1, step)
                elif price <= minimum:
                    price += random.randint(1, step)
                else:
                    price += random.randint(-step, step)
                self.market[item] = max(1, price)
            await self._save_market()

    @market_loop.before_loop
    async def before_market_loop(self) -> None:
        await self.bot.wait_until_ready()

    @tasks.loop(time=dt.time(hour=23, minute=0, tzinfo=dt.timezone.utc))
    async def bank_interest_loop(self) -> None:
        async with self.data_lock:
            changed = False
            for player in self.players.values():
                interest = round(player["bank"] * 0.05)
                if interest > 0:
                    player["bank"] += interest
                    changed = True
            if changed:
                await self._save_players()

    @bank_interest_loop.before_loop
    async def before_bank_interest_loop(self) -> None:
        await self.bot.wait_until_ready()

    # ------------------------------ account ---------------------------------

    @commands.command(name="start")
    async def start(self, ctx: commands.Context) -> None:
        user_id = str(ctx.author.id)
        async with self.data_lock:
            if user_id in self.players:
                await ctx.send("你已經有帳號了。")
                return
            player = self.create_player_data()
            draws = ["SR"]
            draws.extend(random.choices(CARD_TYPES, weights=(75, 20, 10, 5, 1), k=9))
            for card in draws:
                player["cards"][card] += 1
            self.players[user_id] = player
            self._append_log(user_id, self.logs, "create_account")
            await self._save_players()
            await self._save_logs()

        embed = discord.Embed(
            title="新手禮包：十連抽",
            description="```\n" + "\n".join(CARD_NAMES[card] for card in draws) + "\n```",
            colour=EMBED_COLOUR,
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(
            name="開始遊玩",
            value=f"使用 `{ctx.clean_prefix}money` 查看資產，`{ctx.clean_prefix}shop` 查看商店。",
            inline=False,
        )
        await ctx.send(embed=embed)

    @commands.command(name="del_acc", aliases=["delete_account"])
    async def delete_account(self, ctx: commands.Context) -> None:
        if not await self._require_player(ctx):
            return
        message = await ctx.send("確定刪除 Economy 帳號嗎？此操作無法復原。")
        confirmed = await self._confirm(ctx, message)
        if not confirmed:
            await message.edit(content="已取消或確認逾時。")
            return

        async with self.data_lock:
            user_id = str(ctx.author.id)
            if user_id not in self.players:
                await message.edit(content="帳號已不存在。")
                return
            del self.players[user_id]
            self.logs.pop(user_id, None)
            await self._save_players()
            await self._save_logs()
        await message.edit(content="已刪除你的 Economy 帳號。")

    @commands.command(name="money", aliases=["Money", "MONEY", "bal", "Bal", "BAL", "balance"])
    async def balance(self, ctx: commands.Context) -> None:
        if not await self._require_player(ctx):
            return
        async with self.data_lock:
            player = self.players.get(str(ctx.author.id))
            if player is None:
                await ctx.send("帳號狀態已改變，請重新執行指令。")
                return
            wallet = player["wallet"]
            bank = player["bank"]
        embed = discord.Embed(
            title=f"{ctx.author.display_name} 的戶口",
            colour=EMBED_COLOUR,
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(name="錢包", value=f"{wallet:,}")
        embed.add_field(name="銀行", value=f"{bank:,}")
        embed.set_footer(text=ctx.author.display_name, icon_url=self._footer_icon(ctx.author))
        await ctx.send(embed=embed)

    @commands.command(name="log", aliases=["logs"])
    async def economy_log(self, ctx: commands.Context) -> None:
        if not await self._require_player(ctx):
            return
        async with self.data_lock:
            entries = copy.deepcopy(self.logs.get(str(ctx.author.id), [])[-10:])
        if not entries:
            await ctx.send("目前沒有交易紀錄。")
            return
        lines = []
        for entry in reversed(entries):
            details = ", ".join(
                f"{key}={value}" for key, value in entry.items() if key not in {"type", "time"}
            )
            time = entry.get("time", "")[:19].replace("T", " ")
            lines.append(f"`{time}` **{entry.get('type', 'unknown')}** {details}".rstrip())
        embed = discord.Embed(
            title="最近 10 筆 Economy 紀錄",
            description="\n".join(lines)[:4096],
            colour=EMBED_COLOUR,
            timestamp=discord.utils.utcnow(),
        )
        await ctx.send(embed=embed)

    # ------------------------------- money ----------------------------------

    @commands.command(name="work", aliases=["w", "W", "Work", "WORK"])
    @commands.cooldown(1, 6, commands.BucketType.user)
    async def work(self, ctx: commands.Context) -> None:
        if not await self._require_player(ctx):
            self._reset_cooldown(ctx)
            return
        messages = self.config.get("work", {}).get("messages", [])
        if not messages:
            self._reset_cooldown(ctx)
            await ctx.send("目前沒有可用的工作設定。")
            return
        reward = random.randint(100, 499)
        async with self.data_lock:
            player = self.players.get(str(ctx.author.id))
            if player is None:
                self._reset_cooldown(ctx)
                return
            player["wallet"] += reward
            self._append_log(ctx.author.id, self.logs, "work", amount=reward)
            await self._save_players()
            await self._save_logs()
        message = random.choice(messages)
        message = str(message).replace("${amount}", f"${reward:,}")
        message = message.replace("{amount}", f"{reward:,}")
        await ctx.send(message)

    @commands.command(name="fish", aliases=["釣魚", "Fishing", "FISH"])
    @commands.cooldown(1, 60, commands.BucketType.user)
    async def fish(self, ctx: commands.Context) -> None:
        """Catch one weighted item and immediately sell it for wallet money."""
        if not await self._require_player(ctx):
            self._reset_cooldown(ctx)
            return

        fishing = self.config["fishing"]
        item = weighted_choice(fishing["items"])
        rarity = weighted_choice(fishing["rarities"])
        amount = round(item["base_price"] * rarity["price_multiplier"])

        async with self.data_lock:
            player = self.players.get(str(ctx.author.id))
            if player is None:
                self._reset_cooldown(ctx)
                return
            player["wallet"] += amount
            self._append_log(
                ctx.author.id,
                self.logs,
                "fish",
                item=item["id"],
                rarity=rarity["id"],
                amount=amount,
            )
            await self._save_players()
            await self._save_logs()

        embed = discord.Embed(
            title="🎣 釣魚成功",
            description=f"你釣到了 **{rarity['name']} {item['name']}**！",
            colour=EMBED_COLOUR,
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(name="自動出售", value=f"+{amount:,} 塊（已存入錢包）", inline=False)
        embed.set_footer(text="魚獲不進背包，會立即出售；下次釣魚 60 秒後可用。")
        await ctx.send(embed=embed)

    @commands.command(name="daily", aliases=["Daily", "DAILY", "daliy", "Daliy"])
    @commands.cooldown(1, 24 * 60 * 60, commands.BucketType.user)
    async def daily(self, ctx: commands.Context) -> None:
        if not await self._require_player(ctx):
            self._reset_cooldown(ctx)
            return
        cards = random.choices(CARD_TYPES, weights=(70, 20, 10, 5, 1), k=5)
        async with self.data_lock:
            player = self.players.get(str(ctx.author.id))
            if player is None:
                self._reset_cooldown(ctx)
                return
            player["wallet"] += 2500
            for card in cards:
                player["cards"][card] += 1
            self._append_log(ctx.author.id, self.logs, "daily", amount=2500, cards=cards)
            await self._save_players()
            await self._save_logs()
        embed = discord.Embed(
            title="每日簽到：2,500 塊與五連抽",
            description="```\n" + "\n".join(CARD_NAMES[card] for card in cards) + "\n```",
            colour=EMBED_COLOUR,
            timestamp=discord.utils.utcnow(),
        )
        await ctx.send(embed=embed)

    @commands.command(name="hourly", aliases=["Hourly", "HOURLY"])
    @commands.cooldown(1, 60 * 60, commands.BucketType.user)
    async def hourly(self, ctx: commands.Context) -> None:
        if not await self._require_player(ctx):
            self._reset_cooldown(ctx)
            return
        async with self.data_lock:
            player = self.players.get(str(ctx.author.id))
            if player is None:
                self._reset_cooldown(ctx)
                return
            player["wallet"] += 500
            self._append_log(ctx.author.id, self.logs, "hourly", amount=500)
            await self._save_players()
            await self._save_logs()
        await ctx.send("每小時簽到：獲得 500 塊。")

    @commands.command(name="deposit", aliases=["dep", "Dep", "DEP", "dEp"])
    async def deposit(self, ctx: commands.Context, amount: str | None = None) -> None:
        if not await self._require_player(ctx):
            return
        if amount is None:
            await ctx.send("請指定要存入的金額。")
            return
        async with self.data_lock:
            player = self.players.get(str(ctx.author.id))
            if player is None:
                await ctx.send("帳號狀態已改變，請重新執行指令。")
                return
            try:
                parsed = parse_amount(amount, maximum=player["wallet"])
            except ValueError as error:
                await ctx.send(str(error))
                return
            player["wallet"] -= parsed
            player["bank"] += parsed
            self._append_log(ctx.author.id, self.logs, "deposit", amount=parsed)
            await self._save_players()
            await self._save_logs()
        await ctx.send(f"成功存入 {parsed:,} 塊。")

    @commands.command(name="withdraw", aliases=["wd", "Wd", "WD", "Withdraw", "WITHDRAW"])
    async def withdraw(self, ctx: commands.Context, amount: str | None = None) -> None:
        if not await self._require_player(ctx):
            return
        if amount is None:
            await ctx.send("請指定要領出的金額。")
            return
        async with self.data_lock:
            player = self.players.get(str(ctx.author.id))
            if player is None:
                await ctx.send("帳號狀態已改變，請重新執行指令。")
                return
            try:
                parsed = parse_amount(amount, maximum=player["bank"])
            except ValueError as error:
                await ctx.send(str(error))
                return
            player["bank"] -= parsed
            player["wallet"] += parsed
            self._append_log(ctx.author.id, self.logs, "withdraw", amount=parsed)
            await self._save_players()
            await self._save_logs()
        await ctx.send(f"成功領出 {parsed:,} 塊。")

    # ---------------------------- cards / shop ------------------------------

    @commands.command(name="shop", aliases=["Shop", "SHOP"])
    async def shop(self, ctx: commands.Context) -> None:
        embed = discord.Embed(title="商店", colour=EMBED_COLOUR, timestamp=discord.utils.utcnow())
        embed.add_field(name="隨機卡 [random / rd]", value=f"{self.market['random']:,}", inline=False)
        for card in CARD_TYPES:
            embed.add_field(name=CARD_NAMES[card], value=f"{self.market[card]:,}", inline=True)
        embed.add_field(
            name="購買方式",
            value=f"`{ctx.clean_prefix}buy <卡片或 random> <數量>`",
            inline=False,
        )
        embed.set_footer(text="價格每小時更新一次", icon_url=self._footer_icon(ctx.author))
        await ctx.send(embed=embed)

    @commands.command(name="buy", aliases=["Buy", "BUY"])
    async def buy(self, ctx: commands.Context, item: str | None = None, amount: str = "1") -> None:
        if not await self._require_player(ctx):
            return
        if item is None:
            await ctx.send("請指定要購買的卡片。")
            return
        is_random = item.lower() in {"random", "rd"}
        card = normalize_card(item)
        if not is_random and card is None:
            await ctx.send("找不到這個商品。")
            return
        try:
            parsed = parse_amount(amount)
        except ValueError as error:
            await ctx.send(str(error))
            return
        limit = 5 if is_random else 50
        if parsed > limit:
            await ctx.send(f"單次最多購買 {limit} 張。")
            return

        async with self.data_lock:
            player = self.players.get(str(ctx.author.id))
            if player is None:
                await ctx.send("帳號狀態已改變，請重新執行指令。")
                return
            market_key = "random" if is_random else card
            price = self.market[market_key]
            total = price * parsed
            if player["wallet"] < total:
                await ctx.send("錢包餘額不足。")
                return
            player["wallet"] -= total
            if is_random:
                cards = random.choices(CARD_TYPES, weights=(70, 50, 30, 20, 10), k=parsed)
            else:
                cards = [card] * parsed
            for drawn_card in cards:
                player["cards"][drawn_card] += 1
            self._append_log(ctx.author.id, self.logs, "buy", amount=total, cards=cards)
            await self._save_players()
            await self._save_logs()

        embed = self._transaction_embed("購買完成")
        embed.add_field(name="卡片", value="\n".join(CARD_NAMES[value] for value in cards), inline=False)
        embed.add_field(name="總價", value=f"{total:,}")
        await ctx.send(embed=embed)

    @commands.command(name="sell", aliases=["Sell", "SELL", "sale", "Sale", "SALE"])
    async def sell(self, ctx: commands.Context, item: str | None = None, amount: str = "1") -> None:
        if not await self._require_player(ctx):
            return
        card = normalize_card(item)
        if card is None:
            await ctx.send("請指定有效的卡片。")
            return
        async with self.data_lock:
            player = self.players.get(str(ctx.author.id))
            if player is None:
                await ctx.send("帳號狀態已改變，請重新執行指令。")
                return
            try:
                parsed = parse_amount(amount, maximum=player["cards"][card])
            except ValueError as error:
                await ctx.send(str(error))
                return
            income = self.market[card] * parsed
            player["cards"][card] -= parsed
            player["wallet"] += income
            self._append_log(ctx.author.id, self.logs, "sell", card=card, count=parsed, amount=income)
            await self._save_players()
            await self._save_logs()
        await ctx.send(f"賣出 {CARD_NAMES[card]} × {parsed}，獲得 {income:,} 塊。")

    @commands.command(name="army", aliases=["Army", "ARMY"])
    async def army(self, ctx: commands.Context, member: discord.Member | None = None) -> None:
        member = member or ctx.author
        if member.id != ctx.author.id:
            await ctx.send("其他玩家的軍隊資料不公開。")
            return
        if not await self._require_player(ctx):
            return
        async with self.data_lock:
            player = self.players.get(str(member.id))
            if player is None:
                await ctx.send("帳號狀態已改變，請重新執行指令。")
                return
            cards = copy.deepcopy(player["cards"])
            power = self.calculate_army_power(cards)
        lines = [f"{CARD_NAMES[card]} × {cards[card]}" for card in CARD_TYPES]
        embed = discord.Embed(
            title=f"🔰 {member.display_name} 的軍隊",
            description="\n".join(lines),
            colour=EMBED_COLOUR,
            timestamp=discord.utils.utcnow(),
        )
        embed.set_footer(
            text=f"總戰力 ✧ {power:,}",
            icon_url=self._footer_icon(member),
        )
        await ctx.send(embed=embed)

    @commands.command(name="set", aliases=["Set", "SET"])
    async def settings(
        self,
        ctx: commands.Context,
        category: str | None = None,
        item: str | None = None,
        value: str | None = None,
    ) -> None:
        if not await self._require_player(ctx):
            return
        async with self.data_lock:
            player = self.players.get(str(ctx.author.id))
            if player is None:
                await ctx.send("帳號狀態已改變，請重新執行指令。")
                return
            player_snapshot = copy.deepcopy(player)
        if category is None and item is None and value is None:
            attack = self.get_attack_army(player_snapshot)
            defense_settings = player_snapshot["battle"]["defense"]
            if defense_settings["mode"] == "auto":
                ratio_percent = defense_settings["ratio"] * 100
                defense_text = f"自動：持有軍隊的 {ratio_percent:g}%"
            else:
                defense = self.get_defense_army(player_snapshot)
                defense_text = "\n".join(f"{card}: {defense[card]}" for card in CARD_TYPES)
            embed = self._transaction_embed("戰鬥設定")
            embed.add_field(
                name="攻擊部署",
                value="\n".join(f"{card}: {attack[card]}" for card in CARD_TYPES),
            )
            embed.add_field(name="防禦部署", value=defense_text)
            embed.add_field(
                name="用法",
                value=(
                    f"`{ctx.clean_prefix}set attack <卡片/all> <數量>`\n"
                    f"`{ctx.clean_prefix}set defend <卡片/all> <數量>`\n"
                    f"`{ctx.clean_prefix}set defend auto <百分比>`"
                ),
                inline=False,
            )
            await ctx.send(embed=embed)
            return
        if category is None or item is None or value is None:
            await ctx.send("參數不足；不帶參數使用 set 可查看說明。")
            return

        category = category.lower()
        item_lower = item.lower()
        card = normalize_card(item)

        if category in {"defend", "defense", "de"} and item_lower == "auto":
            try:
                ratio = parse_defense_ratio(value)
            except ValueError as error:
                await ctx.send(str(error))
                return
            parsed = None
        else:
            try:
                parsed = parse_non_negative_amount(value)
            except ValueError as error:
                await ctx.send(str(error))
                return
            if parsed > 1_000_000:
                await ctx.send("設定值過大。")
                return

        async with self.data_lock:
            player = self.players.get(str(ctx.author.id))
            if player is None:
                await ctx.send("帳號狀態已改變，請重新執行指令。")
                return
            if category in {"attack", "battle", "at"}:
                if item_lower == "all":
                    for card_type in CARD_TYPES:
                        player["battle"]["attack"][card_type] = parsed
                elif card is not None:
                    player["battle"]["attack"][card] = parsed
                else:
                    await ctx.send("找不到這張卡片。")
                    return
                description = f"攻擊部署已更新為 {parsed}。"
            elif category in {"defend", "defense", "de"}:
                if item_lower == "auto":
                    player["battle"]["defense"]["mode"] = "auto"
                    player["battle"]["defense"]["ratio"] = ratio
                    description = f"自動防禦已設為持有軍隊的 {ratio * 100:g}%。"
                elif item_lower == "all":
                    player["battle"]["defense"]["mode"] = "manual"
                    for card_type in CARD_TYPES:
                        player["battle"]["defense"]["troops"][card_type] = parsed
                    description = f"固定防禦部署已更新為 {parsed}。"
                elif card is not None:
                    player["battle"]["defense"]["mode"] = "manual"
                    player["battle"]["defense"]["troops"][card] = parsed
                    description = f"{CARD_NAMES[card]} 的固定防禦部署已更新為 {parsed}。"
                else:
                    await ctx.send("找不到這張卡片。")
                    return
            else:
                await ctx.send("類別必須是 attack 或 defend。")
                return
            await self._save_players()
        await ctx.send(description)

    # ------------------------- transfer / gambling --------------------------

    @commands.command(
        name="gift",
        aliases=["Gift", "GIFT", "give", "Give", "GIVE", "pay", "Pay", "PAY"],
    )
    @commands.cooldown(1, 20, commands.BucketType.user)
    async def gift(
        self,
        ctx: commands.Context,
        member: discord.Member | None = None,
        item: str | None = None,
        amount: str = "1",
    ) -> None:
        if not await self._require_player(ctx):
            self._reset_cooldown(ctx)
            return
        if member is None or item is None:
            await ctx.send("用法：gift @玩家 <money/金額/卡片> <數量>")
            self._reset_cooldown(ctx)
            return
        if member.id == ctx.author.id:
            await ctx.send("不能送給自己。")
            self._reset_cooldown(ctx)
            return
        if member.bot:
            await ctx.send("不能與機器人交易。")
            self._reset_cooldown(ctx)
            return
        if not self.player_exists(member.id):
            await ctx.send("對方沒有 Economy 帳號。")
            self._reset_cooldown(ctx)
            return

        card = normalize_card(item)
        async with self.data_lock:
            sender = self.players.get(str(ctx.author.id))
            if sender is None:
                await ctx.send("帳號狀態已改變，請重新執行指令。")
                self._reset_cooldown(ctx)
                return
            sender_snapshot = copy.deepcopy(sender)
        try:
            if card is not None:
                parsed = parse_amount(amount, maximum=sender_snapshot["cards"][card])
                transfer_type = "card"
                summary = f"{CARD_NAMES[card]} × {parsed}"
            else:
                money_value = amount if item.lower() in {"money", "cash", "錢", "钱"} else item
                parsed = parse_amount(money_value, maximum=sender_snapshot["bank"])
                transfer_type = "money"
                summary = f"銀行存款 {parsed:,} 塊"
        except ValueError as error:
            await ctx.send(str(error))
            self._reset_cooldown(ctx)
            return

        embed = self._transaction_embed()
        embed.add_field(name="送出", value=summary)
        embed.add_field(name="寄件人 → 收件人", value=f"{ctx.author.mention} → {member.mention}", inline=False)
        embed.set_footer(text="請按 ✅ 確認，或按 ❌ 取消")
        message = await ctx.send(embed=embed)
        confirmed = await self._confirm(ctx, message)
        if not confirmed:
            embed.set_footer(text="已取消或確認逾時")
            await message.edit(embed=embed)
            self._reset_cooldown(ctx)
            return

        async with self.data_lock:
            sender = self.players.get(str(ctx.author.id))
            receiver = self.players.get(str(member.id))
            if sender is None or receiver is None:
                await ctx.send("交易期間其中一個帳號已不存在。")
                return
            if transfer_type == "card":
                if sender["cards"][card] < parsed:
                    await ctx.send("交易期間卡片數量已改變，交易取消。")
                    return
                sender["cards"][card] -= parsed
                receiver["cards"][card] += parsed
            else:
                if sender["bank"] < parsed:
                    await ctx.send("交易期間銀行餘額已改變，交易取消。")
                    return
                sender["bank"] -= parsed
                receiver["bank"] += parsed
            self._append_log(
                ctx.author.id,
                self.logs,
                "gift_sent",
                receiver_id=str(member.id),
                item=card or "money",
                amount=parsed,
            )
            self._append_log(
                member.id,
                self.logs,
                "gift_received",
                sender_id=str(ctx.author.id),
                item=card or "money",
                amount=parsed,
            )
            await self._save_players()
            await self._save_logs()
        embed.set_footer(text="交易完成")
        await message.edit(embed=embed)

    @commands.command(name="bet", aliases=["Bet", "BET"])
    @commands.cooldown(1, 7, commands.BucketType.user)
    async def bet(self, ctx: commands.Context, amount: str | None = None) -> None:
        if not await self._require_player(ctx):
            self._reset_cooldown(ctx)
            return
        if amount is None:
            await ctx.send("請指定下注金額。")
            self._reset_cooldown(ctx)
            return
        async with self.data_lock:
            player = self.players.get(str(ctx.author.id))
            maximum = player["wallet"] if player is not None else 0
        try:
            parsed = parse_amount(amount, maximum=maximum)
        except ValueError as error:
            await ctx.send(str(error))
            self._reset_cooldown(ctx)
            return

        embed = self._transaction_embed("賭博現場")
        embed.add_field(name="下注", value=f"{parsed:,} 塊")
        embed.set_footer(text="請按 ✅ 確認，或按 ❌ 取消")
        message = await ctx.send(embed=embed)
        confirmed = await self._confirm(ctx, message)
        if not confirmed:
            embed.set_footer(text="已取消或確認逾時")
            await message.edit(embed=embed)
            self._reset_cooldown(ctx)
            return

        async with self.data_lock:
            player = self.players.get(str(ctx.author.id))
            if player is None or player["wallet"] < parsed:
                await ctx.send("確認期間餘額已改變，下注取消。")
                return
            won = random.random() < 0.20
            change = parsed * 3 if won else -parsed
            player["wallet"] += change
            self._append_log(ctx.author.id, self.logs, "bet", wager=parsed, change=change)
            await self._save_players()
            await self._save_logs()
        embed.clear_fields()
        embed.add_field(
            name="贏了 🎊" if won else "輸了 😩",
            value=f"{'獲得' if won else '損失'} {abs(change):,} 塊",
        )
        embed.set_footer(text="已結算")
        await message.edit(embed=embed)

    @commands.command(name="rob", aliases=["Rob", "ROB"])
    @commands.cooldown(1, 10, commands.BucketType.user)
    @commands.guild_only()
    async def rob(self, ctx: commands.Context, member: discord.Member | None = None) -> None:
        if not await self._require_player(ctx):
            self._reset_cooldown(ctx)
            return
        if member is None:
            await ctx.send("請指定要搶劫的玩家。")
            self._reset_cooldown(ctx)
            return
        if member.id == ctx.author.id or member.bot:
            await ctx.send("不能搶劫自己或機器人。")
            self._reset_cooldown(ctx)
            return
        if not self.player_exists(member.id):
            await ctx.send("對方沒有 Economy 帳號。")
            self._reset_cooldown(ctx)
            return

        async with self.data_lock:
            robber = self.players.get(str(ctx.author.id))
            victim = self.players.get(str(member.id))
            if robber is None or victim is None:
                await ctx.send("其中一個帳號已不存在。")
                return
            if victim["wallet"] <= 0:
                await ctx.send("對方的錢包是空的。")
                self._reset_cooldown(ctx)
                return

            if random.random() < 0.50:
                stolen = random_fraction(victim["wallet"])
                victim["wallet"] -= stolen
                robber["wallet"] += stolen
                result = f"成功從 {member.display_name} 的錢包拿走 {stolen:,} 塊。"
                self._append_log(ctx.author.id, self.logs, "rob_success", victim_id=str(member.id), amount=stolen)
                self._append_log(member.id, self.logs, "robbed", robber_id=str(ctx.author.id), amount=stolen)
            else:
                account = "wallet" if robber["wallet"] > 0 else "bank"
                fine = random_fraction(robber[account])
                robber[account] -= fine
                result = f"你被警察發現，從{'錢包' if account == 'wallet' else '銀行'}罰款 {fine:,} 塊。"
                self._append_log(ctx.author.id, self.logs, "rob_failed", fine=fine, account=account)
            await self._save_players()
            await self._save_logs()
        await ctx.send(result)

    # ------------------------------- battle ---------------------------------

    @commands.command(name="fight", aliases=["Fight", "FIGHT", "attack", "battle"])
    @commands.cooldown(1, 10 * 60, commands.BucketType.user)
    @commands.guild_only()
    async def fight(self, ctx: commands.Context, member: discord.Member | None = None) -> None:
        if not await self._require_player(ctx):
            self._reset_cooldown(ctx)
            return
        if member is None:
            await ctx.send("請指定攻打對象。")
            self._reset_cooldown(ctx)
            return
        if member.id == ctx.author.id:
            await ctx.send("不能攻打自己。")
            self._reset_cooldown(ctx)
            return
        if member.bot:
            await ctx.send("不能攻打機器人。")
            self._reset_cooldown(ctx)
            return
        if not self.player_exists(member.id):
            await ctx.send("對方沒有 Economy 帳號。")
            self._reset_cooldown(ctx)
            return

        async with self.data_lock:
            attacker = self.players.get(str(ctx.author.id))
            defender = self.players.get(str(member.id))
            if attacker is None or defender is None:
                await ctx.send("帳號狀態已改變，請重新執行指令。")
                self._reset_cooldown(ctx)
                return
            attack_units = self.get_attack_army(attacker)
            attacker_power = self.calculate_army_power(attack_units)
            remaining_protection = self.protection_remaining(defender)
        if attacker_power <= 0:
            await ctx.send("你沒有可部署的兵力。")
            self._reset_cooldown(ctx)
            return
        if remaining_protection > 0:
            await ctx.send(
                f"{member.display_name} 仍在戰鬥保護期，約 "
                f"{dt.timedelta(seconds=remaining_protection)} 後解除。"
            )
            self._reset_cooldown(ctx)
            return

        embed = self._transaction_embed("⚔️ 戰鬥確認")
        embed.add_field(
            name=f"你的攻擊部隊｜戰力 {attacker_power:,}",
            value="\n".join(f"{card} × {attack_units[card]}" for card in CARD_TYPES),
        )
        embed.add_field(
            name="可能風險",
            value=(
                "確認後才會讀取敵方防守配置。\n"
                "勝利仍會損失 10%～25% 部隊；戰敗會損失 30%～50%。\n"
                "戰敗不會額外扣除金錢。"
            ),
            inline=False,
        )
        embed.set_footer(text="敵方軍隊、戰力與勝率會在確認攻擊後公開")
        view = FightConfirmationView(ctx.author.id)
        message = await ctx.send(embed=embed, view=view)
        view.message = message
        await view.wait()
        if view.confirmed is not True:
            embed.set_footer(text="已取消或確認逾時")
            await message.edit(embed=embed, view=view)
            self._reset_cooldown(ctx)
            return

        async with self.data_lock:
            attacker = self.players.get(str(ctx.author.id))
            defender = self.players.get(str(member.id))
            if attacker is None or defender is None:
                await ctx.send("戰鬥期間其中一個帳號已不存在。")
                return
            remaining_protection = self.protection_remaining(defender)
            if remaining_protection > 0:
                await ctx.send("確認期間對方已進入戰鬥保護，攻擊取消。")
                return

            # Fight v2 deliberately reads the defender's configuration only
            # after confirmation, preventing free reconnaissance.
            attack_units = self.get_attack_army(attacker)
            defense_units = self.get_defense_army(defender)
            attacker_power = self.calculate_army_power(attack_units)
            defender_power = self.calculate_army_power(defense_units)
            if attacker_power <= 0:
                await ctx.send("確認期間軍隊狀態已改變，戰鬥取消。")
                return
            if defender_power <= 0 and defender["bank"] <= SAFE_BANK:
                await ctx.send("對方沒有可交戰部隊，銀行也沒有可掠奪資產。")
                return

            win_rate = self.calculate_win_rate(attacker_power, defender_power)
            attacker_won = random.random() < win_rate
            attacker_losses, attacker_loss_rate = self.calculate_casualties(
                attack_units,
                attacker_won,
            )
            defender_losses, defender_loss_rate = self.calculate_casualties(
                defense_units,
                not attacker_won,
            )
            loot = (
                self.calculate_loot(defender["bank"], attacker_power, defender_power)
                if attacker_won
                else 0
            )
            for card in CARD_TYPES:
                attacker["cards"][card] -= attacker_losses[card]
                defender["cards"][card] -= defender_losses[card]
            if loot:
                defender["bank"] -= loot
                attacker["bank"] += loot

            protection_seconds = (
                DEFENSE_DEFEAT_PROTECTION
                if attacker_won
                else DEFENSE_SUCCESS_PROTECTION
            )
            defender["battle"]["protection_until"] = (
                dt.datetime.now(dt.timezone.utc).timestamp() + protection_seconds
            )
            self._append_log(
                ctx.author.id,
                self.logs,
                "fight",
                opponent_id=str(member.id),
                won=attacker_won,
                loot=loot,
                attack_power=attacker_power,
                defense_power=defender_power,
                losses=attacker_losses,
                casualty_rate=round(attacker_loss_rate, 4),
            )
            self._append_log(
                member.id,
                self.logs,
                "defense",
                opponent_id=str(ctx.author.id),
                won=not attacker_won,
                money_lost=loot,
                attack_power=attacker_power,
                defense_power=defender_power,
                losses=defender_losses,
                casualty_rate=round(defender_loss_rate, 4),
                protection_seconds=protection_seconds,
            )
            await self._save_players()
            await self._save_logs()

        embed.title = "⚔️ 戰鬥結果"
        embed.clear_fields()
        embed.add_field(
            name="結果",
            value=f"{ctx.author.display_name} {'勝利 🎊' if attacker_won else '戰敗 😩'}",
            inline=False,
        )
        embed.add_field(
            name=f"攻擊方｜戰力 {attacker_power:,}",
            value=(
                "出戰：\n"
                + "\n".join(f"{card} × {attack_units[card]}" for card in CARD_TYPES)
                + "\n損失：\n"
                + "\n".join(f"{card} × {attacker_losses[card]}" for card in CARD_TYPES)
            ),
        )
        embed.add_field(
            name=f"防守方｜戰力 {defender_power:,}（有效 {defender_power * DEFENSE_POWER_BONUS:,.0f}）",
            value=(
                "出戰：\n"
                + "\n".join(f"{card} × {defense_units[card]}" for card in CARD_TYPES)
                + "\n損失：\n"
                + "\n".join(f"{card} × {defender_losses[card]}" for card in CARD_TYPES)
            ),
        )
        embed.add_field(name="戰利品", value=f"{loot:,} 塊", inline=False)
        embed.add_field(
            name="戰況",
            value=f"{self.describe_advantage(win_rate)}（攻方勝率 {win_rate:.1%}）",
        )
        embed.add_field(
            name="保護期",
            value=f"{member.display_name} 獲得 {protection_seconds // 60} 分鐘保護。",
        )
        embed.set_footer(
            text=(
                f"攻方傷亡 {attacker_loss_rate:.1%}｜"
                f"守方傷亡 {defender_loss_rate:.1%}｜攻擊冷卻 10 分鐘"
            )
        )
        battle_buffer = await self.build_battle_image(
            ctx.author,
            member,
            attacker_power,
            defender_power,
        )
        battle_file = discord.File(battle_buffer, filename="battle.png")
        embed.set_image(url="attachment://battle.png")
        await message.edit(embed=embed, view=view, attachments=[battle_file])

    # ----------------------------- leaderboard ------------------------------

    @commands.command(
        name="leaderboard",
        aliases=["Leaderboard", "LEADERBOARD", "lb", "LB", "Lb", "lB"],
    )
    @commands.guild_only()
    async def leaderboard(self, ctx: commands.Context, limit: int = 10) -> None:
        limit = max(1, min(limit, 25))
        ranking = []
        for user_id, player in self.players.items():
            try:
                member = ctx.guild.get_member(int(user_id))
            except ValueError:
                continue
            if member is not None:
                ranking.append((player["wallet"] + player["bank"], user_id, member.display_name))
        ranking.sort(key=lambda item: (item[0], item[1]), reverse=True)
        if not ranking:
            await ctx.send("這個伺服器還沒有排行榜資料。")
            return
        lines = [
            f"**{index}. {name}** — {amount:,}"
            for index, (amount, _, name) in enumerate(ranking[:limit], start=1)
        ]
        embed = discord.Embed(
            title=f"本伺服器最有錢的 {min(limit, len(lines))} 位玩家",
            description="\n".join(lines),
            colour=EMBED_COLOUR,
            timestamp=discord.utils.utcnow(),
        )
        await ctx.send(embed=embed)

    # ---------------------------- command errors ----------------------------

    async def _send_command_error(self, ctx: commands.Context, error: commands.CommandError) -> None:
        if isinstance(error, commands.CommandOnCooldown):
            await ctx.send(f"請等待 {error.retry_after:.0f} 秒後再試。")
        elif isinstance(error, commands.MemberNotFound):
            await ctx.send("找不到這位玩家。")
            self._reset_cooldown(ctx)
        elif isinstance(error, commands.MissingRequiredArgument):
            await ctx.send(f"缺少參數 `{error.param.name}`，請檢查指令用法。")
        elif isinstance(error, commands.BadArgument):
            await ctx.send("參數格式不正確，請檢查玩家、數量或選項。")
        elif isinstance(error, commands.NoPrivateMessage):
            await ctx.send("這個指令只能在伺服器中使用。")
        else:
            logger.error(
                "Economy command %s failed",
                ctx.command,
                exc_info=(type(error), error, error.__traceback__),
            )
            await ctx.send("Economy 指令執行失敗，請稍後再試。")

    async def cog_command_error(
        self,
        ctx: commands.Context,
        error: commands.CommandError,
    ) -> None:
        await self._send_command_error(ctx, error)

async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Economy(bot))
