"""中文关键词 -> sherpa-onnx wenetspeech KWS 模型的 token 序列.

该模型的建模单元是拼音(声母 + 韵母), 韵母带 Unicode 声调符号, 例:
  打开风扇 -> "d ǎ k āi f ēng sh àn @打开风扇"
"""
from pypinyin import pinyin, Style


def load_tokens(tokens_path):
    tokens = set()
    with open(tokens_path, encoding="utf-8") as f:
        for line in f:
            tokens.add(line.split()[0])
    return tokens


def tokenize(word, tokens=None):
    """把中文词转成模型 token 序列(不含 @ 后缀).

    tokens: load_tokens() 的结果, 传入则校验每个 token 是否存在.
    返回 token 字符串, 如 "d ǎ k āi f ēng sh àn".
    有 token 不在词表中时抛 ValueError.
    """
    inits = [x[0] for x in pinyin(word, style=Style.INITIALS, strict=False)]
    finals = [x[0] for x in pinyin(word, style=Style.FINALS_TONE, strict=False)]
    toks = []
    for i, f in zip(inits, finals):
        if i:
            toks.append(i)
        toks.append(f)
    if tokens is not None:
        missing = [t for t in toks if t not in tokens]
        if missing:
            raise ValueError(f"关键词 {word!r} 的 token {missing} 不在模型词表中")
    return " ".join(toks)


def keywords_file_line(word, tokens=None):
    """生成 keywords.txt 的一行: "<tokens> @<word>\""""
    return f"{tokenize(word, tokens)} @{word}"
