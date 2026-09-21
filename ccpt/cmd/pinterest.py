
import asyncio
import json
import random
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from discord.ext import commands


PINTEREST_RESOURCE_URL = (
  "https://za.pinterest.com/resource/BaseSearchResource/get/"
)
PINTEREST_HEADERS = {
  "Accept": "application/json, text/javascript, */*; q=0.01",
  "Accept-Language": "en-US,en;q=0.9",
  "Referer": "https://za.pinterest.com/",
  "Sec-Fetch-Dest": "empty",
  "Sec-Fetch-Mode": "cors",
  "Sec-Fetch-Site": "same-origin",
  "User-Agent": (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/152.0.0.0 Safari/537.36 Edg/153.0.0.0"
  ),
  "X-Pinterest-Appstate": "active",
  "X-Pinterest-Pws-Handler": "www/search/[scope].js",
  "X-Requested-With": "XMLHttpRequest",
}


class PinterestError(Exception):
  """Raised when Pinterest cannot provide a usable result."""


def fetch_random_pin(keyword):
  source_url = f"/search/pins/?q={quote(keyword, safe='')}"
  data = {
    "options": {
      "query": keyword,
      "scope": "pins",
      "appliedProductFilters": "---",
      "domains": None,
      "user": None,
      "seoDrawerEnabled": False,
      "applied_unified_filters": None,
      "auto_correction_disabled": False,
      "filter_genai": False,
      "journey_depth": None,
      "source_id": None,
      "source_module_id": None,
      "source_url": source_url,
      "static_feed": False,
      "selected_one_bar_modules": None,
      "query_pin_sigs": None,
      "page_size": 25,
      "gated": True,
      "price_max": None,
      "price_min": None,
      "query_image_pins": None,
      "request_params": None,
      "top_pin_ids": None,
      "article": None,
      "corpus": None,
      "filters": None,
      "rs": "direct_navigation",
    },
    "context": {},
  }
  query = (
    f"source_url={quote(source_url, safe='')}"
    f"&data={quote(json.dumps(data, separators=(',', ':'), ensure_ascii=False), safe='')}"
    f"&_={int(time.time())}"
  )
  request = Request(
    f"{PINTEREST_RESOURCE_URL}?{query}",
    headers=PINTEREST_HEADERS,
    method="GET",
  )

  try:
    with urlopen(request, timeout=15) as response:
      payload = json.loads(response.read().decode("utf-8"))
  except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as error:
    raise PinterestError from error

  results = payload.get("resource_response", {}).get("data", {}).get("results", [])
  urls = [
    pin.get("images", {}).get("orig", {}).get("url")
    for pin in results
    if isinstance(pin, dict)
  ]
  urls = [url for url in urls if isinstance(url, str) and url.startswith("http")]
  if not urls:
    raise PinterestError
  return random.choice(urls)


class Pinterest(commands.Cog):
  def __init__(self, bot):
    self.bot = bot

  @commands.command(name="picture", aliases=["pic"])
  @commands.cooldown(1, 5, commands.BucketType.user)
  async def pic(self, ctx, *, keyword=None):
    """搜尋 Pinterest 並隨機回傳一張圖片的 URL。"""
    keyword = (keyword or "").strip()
    if not keyword:
      self.pic.reset_cooldown(ctx)
      await ctx.send("用法：`ai-picture <關鍵字>` 或 `ai-pic <關鍵字>`")
      return

    try:
      image_url = await asyncio.to_thread(fetch_random_pin, keyword)
    except PinterestError:
      await ctx.send("https://cdn.discordapp.com/attachments/942418770088063047/1289393633694453800/nknow.png?ex=6aab3de6&is=6aa9ec66&hm=f3a5e0778a04e2ae1241d1d80cc0d9e002212ba1a8d0300401ba91cb23c48559")
      return
    await ctx.send(image_url)

  @pic.error
  async def pic_error(self, ctx, error):
    if isinstance(error, commands.CommandOnCooldown):
      await ctx.send(f"這個指令還在冷卻中，請 {error.retry_after:.1f} 秒後再使用。")


async def setup(bot):
  await bot.add_cog(Pinterest(bot))
