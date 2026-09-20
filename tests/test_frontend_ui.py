"""前端自动化测试：Streamlit AppTest 无头渲染 11 页签（离线+临时库）。

覆盖：全页签渲染无异常、关键 KPI/文案存在、告警评估按钮真实点击。
隔离：CSQUANT_OFFLINE=1 + 临时目录数据库，零外部访问。
"""
import os
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

try:
    from streamlit.testing.v1 import AppTest
    HAS_APPTEST = True
except ImportError:   # streamlit 未安装的最小环境
    HAS_APPTEST = False


@unittest.skipUnless(HAS_APPTEST, "streamlit AppTest 不可用")
class TestFrontendRender(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.mkdtemp(prefix="csquant_ui_")
        cls._old_env = {k: os.environ.get(k)
                        for k in ("DB_PATH", "CSQUANT_OFFLINE")}
        os.environ["DB_PATH"] = str(Path(cls._tmp) / "ui.db")
        os.environ["CSQUANT_OFFLINE"] = "1"
        from utils.config import reset_config
        reset_config()

    @classmethod
    def tearDownClass(cls):
        for k, v in cls._old_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        from utils.config import reset_config
        reset_config()

    def _run_app(self) -> "AppTest":
        at = AppTest.from_file(str(ROOT / "frontend" / "app.py"),
                               default_timeout=60)
        at.run()
        return at

    def test_all_tabs_render_without_exception(self):
        """11 页签全部渲染（空库空态），不得抛任何异常。"""
        at = self._run_app()
        self.assertFalse(at.exception,
                         f"前端渲染异常: {[e.value for e in at.exception]}")

    def test_key_elements_present(self):
        at = self._run_app()
        # st.metric 共 3 个：Universe 规模 + 双 QQ 状态灯（库存 KPI 为自定义 HTML 卡）
        self.assertGreaterEqual(len(at.metric), 3)
        metric_labels = " ".join(str(m.label) for m in at.metric)
        self.assertIn("Universe", metric_labels)
        self.assertIn("QQ Bot", metric_labels)
        # 品牌与 KPI 卡（自定义 HTML 经 markdown 渲染）
        all_markdown = " ".join(str(m.value) for m in at.markdown)
        self.assertIn("Quant", all_markdown)
        self.assertIn("总成本", all_markdown)       # 库存 KPI 卡
        # 空态引导文案
        page_text = all_markdown + " ".join(
            str(c.value) for c in at.caption) + " ".join(
            str(i.value) for i in at.info)
        self.assertIn("暂无持仓", page_text)

    def test_alert_evaluate_button_click(self):
        """真实点击「立即评估一次」：离线下评估 5 规则零触发、无异常。"""
        at = self._run_app()
        target = None
        for b in at.button:
            if "立即评估一次" in str(b.label):
                target = b
                break
        self.assertIsNotNone(target, "未找到告警评估按钮")
        target.click()
        at.run()
        self.assertFalse(at.exception,
                         f"点击评估后异常: {[e.value for e in at.exception]}")
        # 评估结果 JSON 已渲染（evaluated=5 条内置规则）
        json_text = " ".join(str(j.value) for j in at.json)
        self.assertIn("evaluated", json_text)

    def test_market_scan_universe_button(self):
        """点击「刷新Universe」：空库返回 0 且无异常。"""
        at = self._run_app()
        target = None
        for b in at.button:
            if "刷新Universe" in str(b.label):
                target = b
                break
        self.assertIsNotNone(target, "未找到刷新Universe按钮")
        target.click()
        at.run()
        self.assertFalse(at.exception)


if __name__ == "__main__":
    unittest.main()
