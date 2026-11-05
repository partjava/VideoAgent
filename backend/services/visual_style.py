import re


PHOTO_RULES = (
    "真人演员实景拍摄，摄影机记录的真实人物与环境。"
    "自然五官和头身比例，皮肤保留毛孔与细微纹理，头发为真实发丝，"
    "服装和道具呈现实物材质，光影符合现场光源。"
)
PHOTO_EXCLUSIONS = "动漫，漫画，卡通，插画，二次元，赛璐璐，三渲二，游戏CG，3D建模人物，蜡像，塑料皮肤"


def lock_visual_style(prompt, style):
    if style != "短剧":
        return prompt
    prompt = str(prompt or "").removeprefix(PHOTO_RULES + "\n")
    text = re.sub(
        r"日系赛璐璐|韩漫厚涂|国漫水墨|二次元风格|漫画风格|动漫风格|"
        r"插画风格|卡通风格|3D渲染风格|游戏CG风格", "", prompt,
    )
    return PHOTO_RULES + "\n" + text
