"""Гуманоидный скелет: слоты, синонимы имён костей и авто-сопоставление.

Смысл слоя: движение мы снимаем в терминах человеческого тела (плечо, локоть,
кисть…), а рига у каждой модели свой. Между ними стоит таблица слотов: чтобы
подключить чужую модель, достаточно указать, какая её кость какому слоту
соответствует — руками на экране «Скелет» или автоматически по именам.
"""
from __future__ import annotations

import re

# Слот → (человеческое название, обязателен ли для съёмки движения)
SLOTS: dict[str, tuple[str, bool]] = {
    'hips': ('Таз', True),
    'spine': ('Поясница', False),
    'chest': ('Грудь', False),
    'neck': ('Шея', False),
    'head': ('Голова', False),
    'left_shoulder': ('Ключица слева', False),
    'left_upper_arm': ('Плечо слева', True),
    'left_lower_arm': ('Предплечье слева', True),
    'left_hand': ('Кисть слева', False),
    'right_shoulder': ('Ключица справа', False),
    'right_upper_arm': ('Плечо справа', True),
    'right_lower_arm': ('Предплечье справа', True),
    'right_hand': ('Кисть справа', False),
    'left_upper_leg': ('Бедро слева', True),
    'left_lower_leg': ('Голень слева', True),
    'left_foot': ('Стопа слева', False),
    'right_upper_leg': ('Бедро справа', True),
    'right_lower_leg': ('Голень справа', True),
    'right_foot': ('Стопа справа', False),
}

#: Как слот выглядит в разных ригах: Meshy, Mixamo, VRM, Unreal, Rigify и просто
#: «как назвал художник». Сравнение идёт по нормализованному имени.
ALIASES: dict[str, tuple[str, ...]] = {
    'hips': ('hips', 'pelvis', 'root', 'bip01pelvis', 'j_bip_c_hips'),
    'spine': ('spine', 'spine01', 'spine1', 'abdomen', 'j_bip_c_spine'),
    'chest': ('spine02', 'spine2', 'chest', 'upperchest', 'j_bip_c_chest', 'torso'),
    'neck': ('neck', 'j_bip_c_neck'),
    'head': ('head', 'j_bip_c_head'),
    'left_shoulder': ('leftshoulder', 'lshoulder', 'shoulderl', 'clavicle_l', 'j_bip_l_shoulder'),
    'left_upper_arm': ('leftarm', 'lupperarm', 'upperarml', 'upperarm_l', 'armupperl', 'j_bip_l_upperarm', 'lefthumerus'),
    'left_lower_arm': ('leftforearm', 'llowerarm', 'lowerarml', 'lowerarm_l', 'forearml', 'j_bip_l_lowerarm', 'leftradius'),
    'left_hand': ('lefthand', 'lhand', 'handl', 'hand_l', 'j_bip_l_hand'),
    'right_shoulder': ('rightshoulder', 'rshoulder', 'shoulderr', 'clavicle_r', 'j_bip_r_shoulder'),
    'right_upper_arm': ('rightarm', 'rupperarm', 'upperarmr', 'upperarm_r', 'armupperr', 'j_bip_r_upperarm', 'righthumerus'),
    'right_lower_arm': ('rightforearm', 'rlowerarm', 'lowerarmr', 'lowerarm_r', 'forearmr', 'j_bip_r_lowerarm', 'rightradius'),
    'right_hand': ('righthand', 'rhand', 'handr', 'hand_r', 'j_bip_r_hand'),
    'left_upper_leg': ('leftupleg', 'lupperleg', 'upperlegl', 'thigh_l', 'thighl', 'j_bip_l_upperleg', 'leftthigh'),
    'left_lower_leg': ('leftleg', 'llowerleg', 'lowerlegl', 'calf_l', 'shinl', 'j_bip_l_lowerleg', 'leftshin'),
    'left_foot': ('leftfoot', 'lfoot', 'footl', 'foot_l', 'j_bip_l_foot'),
    'right_upper_leg': ('rightupleg', 'rupperleg', 'upperlegr', 'thigh_r', 'thighr', 'j_bip_r_upperleg', 'rightthigh'),
    'right_lower_leg': ('rightleg', 'rlowerleg', 'lowerlegr', 'calf_r', 'shinr', 'j_bip_r_lowerleg', 'rightshin'),
    'right_foot': ('rightfoot', 'rfoot', 'footr', 'foot_r', 'j_bip_r_foot'),
}

#: Цепочки «родитель → ребёнок»: по ним считается направление кости.
CHAINS: tuple[tuple[str, str], ...] = (
    ('left_upper_arm', 'left_lower_arm'),
    ('left_lower_arm', 'left_hand'),
    ('right_upper_arm', 'right_lower_arm'),
    ('right_lower_arm', 'right_hand'),
    ('left_upper_leg', 'left_lower_leg'),
    ('left_lower_leg', 'left_foot'),
    ('right_upper_leg', 'right_lower_leg'),
    ('right_lower_leg', 'right_foot'),
    ('hips', 'chest'),
    ('chest', 'head'),
)


def normalize(name: str) -> str:
    """`mixamorig:LeftArm.001` → `leftarm`: убираем префиксы рига и разделители."""
    n = name.lower()
    n = re.sub(r'^(mixamorig\d*[:_]|armature[:_]|bip\d*[:_]|def[-_]|org[-_])', '', n)
    n = re.sub(r'\.\d+$', '', n)
    return re.sub(r'[^a-z0-9]', '', n)


def guess_mapping(bone_names: list[str]) -> dict[str, str | None]:
    """Сопоставить кости слотам по именам. Что не угадалось — None, доводит человек."""
    norm = {name: normalize(name) for name in bone_names}
    used: set[str] = set()
    mapping: dict[str, str | None] = {}
    for slot, aliases in ALIASES.items():
        found = None
        for alias in aliases:
            for name, n in norm.items():
                if name in used:
                    continue
                if n == alias:
                    found = name
                    break
            if found:
                break
        if not found:  # второй заход: вхождение, а не точное совпадение
            for alias in aliases:
                for name, n in norm.items():
                    if name in used or len(n) < 3:
                        continue
                    if alias in n or n in alias:
                        found = name
                        break
                if found:
                    break
        if found:
            used.add(found)
        mapping[slot] = found
    return mapping


def missing_required(mapping: dict[str, str | None]) -> list[str]:
    """Слоты, без которых снимать движение бессмысленно."""
    return [slot for slot, (_, required) in SLOTS.items() if required and not mapping.get(slot)]
