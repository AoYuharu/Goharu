// 生成严格的 26列x20行 关卡数据 -> levels.js
const fs = require('fs');
const C = 26, R = 20;

// 工具: 创建空地图(外圈砖墙)
function emptyMap() {
    const g = [];
    for (let y = 0; y < R; y++) {
        let row = '';
        for (let x = 0; x < C; x++) {
            row += (y === 0 || y === R - 1 || x === 0 || x === C - 1) ? 'B' : '.';
        }
        g.push(row);
    }
    return g;
}
function set(g, x, y, ch) {
    if (x > 0 && x < C - 1 && y > 0 && y < R - 1) {
        g[y] = g[y].substring(0, x) + ch + g[y].substring(x + 1);
    }
}
// 竖墙段
function vwall(g, x, y0, y1, ch = 'B') { for (let y = y0; y <= y1; y++) set(g, x, y, ch); }
// 横墙段
function hwall(g, y, x0, x1, ch = 'B') { for (let x = x0; x <= x1; x++) set(g, y, x, ch); }
// 矩形块
function block(g, x0, y0, x1, y1, ch = 'B') {
    for (let y = y0; y <= y1; y++) for (let x = x0; x <= x1; x++) set(g, x, y, ch);
}
// 在地图底部中央放置基地X(3格宽,含两侧砖保护)
function placeBase(g) {
    const bx = Math.floor(C / 2), by = R - 2;
    set(g, bx, by, 'X');
    // 基地周围砖保护
    set(g, bx - 1, by, 'B'); set(g, bx + 1, by, 'B');
    set(g, bx - 1, by - 1, 'B'); set(g, bx, by - 1, 'B'); set(g, bx + 1, by - 1, 'B');
}
// 确保玩家出生区(底部中央)和敌人出生区(顶部三个点)是空的
function clearSpawns(g) {
    // 玩家: 底部中央3x3 (y = R-5)
    const px = Math.floor(C / 2), py = R - 5;
    for (let dy = -1; dy <= 1; dy++) for (let dx = -1; dx <= 1; dx++) {
        const ch = g[py + dy][px + dx];
        if (ch === 'B' || ch === 'S' || ch === 'W' || ch === 'F') set(g, px + dx, py + dy, '.');
    }
    // 敌人: 顶部三个 3x4
    for (const ex of [2, Math.floor(C / 2), C - 3]) {
        for (let dy = 0; dy <= 2; dy++) for (let dx = -1; dx <= 1; dx++) {
            const ch = g[1 + dy][ex + dx];
            if (ch === 'B' || ch === 'S' || ch === 'W' || ch === 'F') set(g, ex + dx, 1 + dy, '.');
        }
    }
}

const levels = [];

// ---- L1: 新手 - 简单砖墙柱 ----
{
    const g = emptyMap();
    for (let x = 4; x <= 20; x += 4) vwall(g, x, 5, 8, 'B');
    for (let x = 4; x <= 20; x += 4) vwall(g, x, 11, 14, 'B');
    placeBase(g); clearSpawns(g);
    levels.push(g);
}
// ---- L2: 砖墙十字 ----
{
    const g = emptyMap();
    hwall(g, 9, 5, 20); vwall(g, 12, 3, 16);
    hwall(g, 13, 5, 20);
    placeBase(g); clearSpawns(g);
    levels.push(g);
}
// ---- L3: 钢柱阵 ----
{
    const g = emptyMap();
    for (let y = 4; y <= 14; y += 3) for (let x = 4; x <= 21; x += 4) set(g, x, y, 'S');
    block(g, 4, 9, 8, 10, 'B'); block(g, 17, 9, 21, 10, 'B');
    placeBase(g); clearSpawns(g);
    levels.push(g);
}
// ---- L4: 迷宫初型 ----
{
    const g = emptyMap();
    vwall(g, 6, 3, 8); vwall(g, 6, 11, 16);
    vwall(g, 12, 5, 10); vwall(g, 12, 13, 14);
    vwall(g, 18, 3, 8); vwall(g, 18, 11, 16);
    hwall(g, 9, 8, 17);
    placeBase(g); clearSpawns(g);
    levels.push(g);
}
// ---- L5: 钢墙要塞 ----
{
    const g = emptyMap();
    hwall(g, 4, 3, 9, 'S'); hwall(g, 4, 16, 22, 'S');
    hwall(g, 15, 3, 9, 'S'); hwall(g, 15, 16, 22, 'S');
    block(g, 10, 8, 15, 11, 'B');
    vwall(g, 12, 3, 6, 'B');
    placeBase(g); clearSpawns(g);
    levels.push(g);
}
// ---- L6: 水道 ----
{
    const g = emptyMap();
    hwall(g, 8, 3, 11, 'W'); hwall(g, 8, 14, 22, 'W');
    hwall(g, 12, 3, 8, 'W'); hwall(g, 12, 17, 22, 'W');
    block(g, 11, 4, 14, 6, 'B');
    block(g, 11, 14, 14, 16, 'B');
    placeBase(g); clearSpawns(g);
    levels.push(g);
}
// ---- L7: 森林伏击 ----
{
    const g = emptyMap();
    for (let y = 3; y <= 16; y += 4) for (let x = 3; x <= 22; x += 2) set(g, x, y, 'F');
    block(g, 6, 8, 8, 11, 'B'); block(g, 17, 8, 19, 11, 'B');
    placeBase(g); clearSpawns(g);
    levels.push(g);
}
// ---- L8: 中央湖 ----
{
    const g = emptyMap();
    block(g, 9, 7, 16, 12, 'W');
    vwall(g, 5, 4, 15, 'B'); vwall(g, 20, 4, 15, 'B');
    set(g, 12, 15, 'S'); set(g, 13, 15, 'S');
    placeBase(g); clearSpawns(g);
    levels.push(g);
}
// ---- L9: 钢齿阵 ----
{
    const g = emptyMap();
    for (let x = 3; x <= 22; x += 3) { set(g, x, 5, 'S'); set(g, x, 9, 'B'); set(g, x, 13, 'S'); }
    hwall(g, 11, 10, 15, 'B');
    placeBase(g); clearSpawns(g);
    levels.push(g);
}
// ---- L10: 水环 ----
{
    const g = emptyMap();
    hwall(g, 6, 7, 18, 'W'); hwall(g, 13, 7, 18, 'W');
    vwall(g, 6, 6, 13, 'W'); vwall(g, 19, 6, 13, 'W');
    block(g, 10, 9, 15, 10, 'B');
    vwall(g, 3, 4, 15, 'B'); vwall(g, 22, 4, 15, 'B');
    placeBase(g); clearSpawns(g);
    levels.push(g);
}
// ---- L11: 密林钢影 ----
{
    const g = emptyMap();
    for (let y = 3; y <= 16; y += 2) for (let x = 3; x <= 22; x += 5) set(g, x, y, 'F');
    for (let y = 4; y <= 15; y += 4) for (let x = 6; x <= 20; x += 5) set(g, x, y, 'S');
    block(g, 11, 8, 14, 11, 'B');
    placeBase(g); clearSpawns(g);
    levels.push(g);
}
// ---- L12: 双桥 ----
{
    const g = emptyMap();
    hwall(g, 9, 2, 23, 'W');
    hwall(g, 10, 2, 23, 'W');
    set(g, 6, 9, '.'); set(g, 7, 9, '.'); set(g, 6, 10, '.'); set(g, 7, 10, '.');
    set(g, 18, 9, '.'); set(g, 19, 9, '.'); set(g, 18, 10, '.'); set(g, 19, 10, '.');
    block(g, 4, 13, 8, 15, 'B'); block(g, 17, 13, 21, 15, 'B');
    block(g, 10, 4, 15, 6, 'S');
    placeBase(g); clearSpawns(g);
    levels.push(g);
}
// ---- L13: 迷宫深处 ----
{
    const g = emptyMap();
    for (let y = 3; y <= 16; y += 3) hwall(g, y, 3, 11, 'B');
    for (let y = 4; y <= 17; y += 3) hwall(g, y, 14, 22, 'B');
    vwall(g, 12, 6, 9, 'S'); vwall(g, 13, 11, 14, 'S');
    placeBase(g); clearSpawns(g);
    levels.push(g);
}
// ---- L14: 堡垒攻坚 ----
{
    const g = emptyMap();
    hwall(g, 5, 8, 17, 'S');
    vwall(g, 8, 5, 12, 'S'); vwall(g, 17, 5, 12, 'S');
    hwall(g, 12, 8, 17, 'S');
    block(g, 11, 7, 14, 10, 'B');
    block(g, 3, 15, 7, 17, 'B'); block(g, 18, 15, 22, 17, 'B');
    placeBase(g); clearSpawns(g);
    levels.push(g);
}
// ---- L15: 终极试炼 ----
{
    const g = emptyMap();
    for (let y = 4; y <= 15; y += 2) { hwall(g, y, 3, 6, y % 4 === 0 ? 'S' : 'B'); hwall(g, y, 19, 22, y % 4 === 0 ? 'S' : 'B'); }
    for (let x = 9; x <= 16; x += 3) vwall(g, x, 4, 7, 'B');
    for (let x = 9; x <= 16; x += 3) vwall(g, x, 12, 15, 'S');
    hwall(g, 9, 9, 16, 'W'); hwall(g, 10, 9, 16, 'W');
    placeBase(g); clearSpawns(g);
    levels.push(g);
}

// 验证并输出
let ok = true;
levels.forEach((g, i) => {
    const badRows = g.filter(r => r.length !== C).length;
    if (g.length !== R || badRows > 0) { console.log(`L${i + 1} 无效! 行数=${g.length}, 坏行=${badRows}`); ok = false; }
});
if (!ok) { console.log('生成失败'); process.exit(1); }

// 写入levels.js
const js = '// Tank Battle Levels - 自动生成 26x20\n// B=砖 S=钢 W=水 F=林 .=空 X=基地\nconst LEVELS=[\n' +
    levels.map(g => JSON.stringify(g)).join(',\n') + '\n];\n';
fs.writeFileSync('levels.js', js);
console.log('生成成功: ' + levels.length + ' 关, 全部 26x20');
