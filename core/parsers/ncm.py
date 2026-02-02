from re import Match
from typing import ClassVar

from aiohttp import ClientError

from astrbot.api import logger

from ..config import PluginConfig
from ..cookie import CookieJar
from ..data import Platform
from ..download import Downloader
from ..exception import ParseException
from .base import BaseParser, handle


class NCMParser(BaseParser):
    """网易云音乐解析器"""

    platform: ClassVar[Platform] = Platform(
        name="ncm", display_name="网易云"
    )

    def __init__(self, config: PluginConfig, downloader: Downloader):
        super().__init__(config, downloader)
        self.headers.update({"Referer": "https://music.163.com"})
        self.mycfg = config.parser.ncm
        self.cookiejar = CookieJar(config, self.mycfg, domain="music.163.com")
        if self.cookiejar.cookies_str:
            self.headers["cookie"] = self.cookiejar.cookies_str


    @handle("163cn.tv", r"163cn\.tv/(?P<short_key>\w+)")
    async def _parse_short(self, searched: Match[str]):
        """解析短链接"""
        short_url = f"https://163cn.tv/{searched.group('short_key')}"
        # 让框架跟随 302 后再走通用解析
        return await self.parse_with_redirect(short_url)


    @handle("y.music.163.com", r"y\.music\.163\.com/m/song\?.*id=(?P<song_id>\d+)")
    @handle("music.163.com/#/song", r"music\.163\.com/#/song\?.*id=(?P<song_id>\d+)")
    @handle("music.163.com/song", r"music\.163\.com/song\?.*id=(?P<song_id>\d+)")
    async def _parse_song(self, searched: Match[str]):
        """解析歌曲链接"""
        song_id = searched.group("song_id")
        detail_url = (
            f"https://music.163.com/api/song/detail/?id={song_id}&ids=[{song_id}]"
        )
        play_url = f"https://music.163.com/api/song/enhance/player/url?ids=[{song_id}]&br=320000"

        # 1. 取歌曲元数据
        async with self.session.get(detail_url, headers=self.headers) as resp:
            if resp.status >= 400:
                raise ClientError(f"[NCM] 获取歌曲信息失败 HTTP {resp.status}")
            detail_json = await resp.json(content_type=None)
            logger.debug(f"[NCM] 歌曲详情: {detail_json}")

        songs = detail_json.get("songs", [])
        if not songs:
            raise ParseException("[NCM] 未找到该歌曲")
        
        song = songs[0]
        title = song.get("name", "")
        sub_title = song.get("alias", [""])[0] if song.get("alias") else ""
        album_name = song.get("album", {}).get("name", "")
        cover_url = song.get("album", {}).get("picUrl", "")
        if cover_url:
            cover_url = cover_url + "?param=640y640"
        duration_ms = song.get("duration", 0)

        # 作者信息
        ar_list = song.get("artists", [])
        author_name = " / ".join(ar.get("name", "") for ar in ar_list)
        author_avatar = ar_list[0].get("img1v1Url", "") if ar_list else ""

        # 2. 取播放地址
        async with self.session.get(play_url, headers=self.headers) as resp:
            if resp.status >= 400:
                raise ClientError(f"[NCM] 获取播放地址失败 HTTP {resp.status}")
            play_json = await resp.json(content_type=None)
            logger.debug(f"[NCM] 播放地址响应: {play_json}")
        
        play_data = play_json.get("data", [])
        if not play_data:
            raise ParseException("[NCM] 获取播放数据失败")
        
        play_info = play_data[0]
        audio_url = play_info.get("url")
        
        # 检查播放地址是否有效
        if not audio_url:
            code = play_info.get("code", -1)
            if code == 404:
                raise ParseException("[NCM] 该歌曲需要VIP或存在版权限制，无法获取播放地址")
            else:
                raise ParseException(f"[NCM] 无法获取播放地址 (code={code})")

        # 3. 组装结果
        author = self.create_author(author_name, author_avatar) if author_name else None
        
        # 使用 create_audio_content 而不是 create_video_content
        audio = self.create_audio_content(audio_url, duration=duration_ms // 1000)
        
        # 下载封面图片
        contents = []
        if cover_url:
            contents.extend(self.create_image_contents([cover_url]))
        contents.append(audio)

        # 4. 返回
        return self.result(
            title=f"{title}{'（' + sub_title + '）' if sub_title else ''}",
            text=f"专辑：{album_name}" if album_name else None,
            author=author,
            contents=contents,
            timestamp=None,
            url=f"https://music.163.com/#/song?id={song_id}",
        )

    @handle("music.126.net", r"https?://[^/]*music\.126\.net/.*\.mp3(?:\?.*)?")
    async def _parse_direct_mp3(self, searched: Match[str]):
        """解析直链 MP3"""
        url = searched.group(0)
        audio = self.create_audio_content(url)
        return self.result(
            title="网易云音乐",
            text="直链音频",
            contents=[audio],
            url=url,
        )

    @handle(
        "music.163.com/song/media/outer/url",
        r"https?://music\.163\.com/song/media/outer/url\?[^>\s]+",
    )
    async def _parse_private_outer(self, searched: Match[str]):
        """解析私人外链"""
        private_url = searched.group(0)
        logger.debug(f"[NCM] 私人外链: {private_url}")
        audio = self.create_audio_content(private_url)
        return self.result(
            title="网易云音乐（私人直链）",
            text="直链音频",
            contents=[audio],
            url=private_url,
        )
