# -*- coding: utf-8 -*-
"""
SimHash 近似重复（simhash.py）
=============================
R007 规则用：判断两条评价"是不是同一句话改了改"。

为什么不用普通哈希：
    普通哈希有"雪崩效应"——改一个字符，整个哈希全变，无法比相似度。
    SimHash 是"局部敏感哈希"——改一两个字符，指纹只变 1~2 位，
    所以可以用"汉明距离"（两个指纹不同的位数）衡量相似度。

算法（64 位）：
    1. 把文本切成 token（中文用 2 字滑窗）
    2. 每个 token 哈希成 64 位整数
    3. 逐位累加：该位是 1 就 +权重，是 0 就 -权重
    4. 最后每位看总和：>0 记 1，否则记 0 → 得到 64 位指纹

判定：
    两个指纹的汉明距离 ≤ 3 → 近似重复。
    （64 位里只有 3 位不同，说明文本高度重合）
"""
import hashlib
from collections import Counter


def _hash_token(token: str) -> int:
    """把一个 token 哈希成 64 位整数。

    用 blake2b 而不是 md5：
        不是为了安全，只是要一个"稳定、均匀"的 64 位值。
        digest_size=8 正好 8 字节 = 64 位。
    """
    digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, "big")


def simhash(text: str, ngram: int = 2) -> int:
    """计算文本的 64 位 simhash 指纹。

    :param text:  归一化后的文本
    :param ngram: 切 token 的窗口大小。中文默认 2（"质量不错" → 质量/量不/不错）
    :return: 64 位整数指纹（相同文本一定相同）
    """
    if not text:
        return 0

    # 1. 切 token：字符 n-gram 滑窗
    #    文本太短（< ngram）时，整段当一个 token
    if len(text) < ngram:
        tokens = [text]
    else:
        tokens = [text[i:i + ngram] for i in range(len(text) - ngram + 1)]

    # 2. 统计每个 token 出现次数（频次即权重）
    freq = Counter(tokens)

    # 3. 逐位加权投票
    #    bits[i] 存第 i 位的"票数"
    bits = [0] * 64
    for token, weight in freq.items():
        h = _hash_token(token)
        for i in range(64):
            if (h >> i) & 1:
                bits[i] += weight   # 该位是 1 → 加票
            else:
                bits[i] -= weight   # 该位是 0 → 减票

    # 4. 票数为正记 1，否则记 0
    result = 0
    for i in range(64):
        if bits[i] > 0:
            result |= (1 << i)
    return result


def hamming(a: int, b: int) -> int:
    """两个指纹的汉明距离（不同的位数）。

    例：a=0b1010, b=0b1000 → 只有第 2 位不同 → 距离 1
    """
    return bin(a ^ b).count("1")


def is_near_duplicate(a: int, b: int, threshold: int = 3) -> bool:
    """两个指纹是否近似重复。"""
    return hamming(a, b) <= threshold


# ==================== 更适合短文本的：Jaccard 相似度 ====================

def char_bigrams(text: str) -> set[str]:
    """把文本切成"字符二元组"集合。

    例："质量不错" → {"质量", "量不", "不错"}
    """
    if not text:
        return set()
    if len(text) < 2:
        return {text}
    return {text[i:i + 2] for i in range(len(text) - 1)}


def jaccard(a: str, b: str) -> float:
    """两个文本的字符 bigram Jaccard 相似度（0~1）。

    公式：|交集| / |并集|
        1.0 = 完全相同
        0.0 = 毫无重合

    为什么用它替代 simhash 做 R007：
        电商评价普遍很短（10 来个字），simhash 的 64 位投票
        在 token 少时非常不稳定（实测改一个字就跳 8~14 位）。
        Jaccard 直接看"共享了多少 bigram"，短文本上也稳定：
            完全相同       → 1.00
            只加个标点     → 0.89
            改一个字       → 0.60
            完全不同       → 0.00
    """
    sa, sb = char_bigrams(a), char_bigrams(b)
    if not sa and not sb:
        return 1.0
    union = len(sa | sb)
    if union == 0:
        return 0.0
    return len(sa & sb) / union
