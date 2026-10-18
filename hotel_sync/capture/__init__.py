"""流程录制（控件捕获）包 —— 仅管理员版。

import 本包即要求管理员版身份；业务员版发行包不包含本包。
"""
from ..editions import require_admin

require_admin("流程录制")
