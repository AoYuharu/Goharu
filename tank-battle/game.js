// 坦克大战 - 游戏核心 v3 (修复版)
// 修复: 敌人出生卡墙/碰撞检测/速度手感/道具掉落率
const TILE=32,COLS=26,ROWS=20;
const W=TILE*COLS,H=TILE*ROWS;
const EMPTY=0,BRICK=1,STEEL=2,WATER=3,FOREST=4,BASE=5,BASE_D=6;
const DX=[0,1,0,-1],DY=[-1,0,1,0];
const S_START=0,S_PLAY=1,S_PAUSE=2,S_OVER=3,S_WIN=4;

// 道具类型
const P_STAR='star',P_BOMB='bomb',P_FREEZE='freeze',P_SHIELD='shield',P_HEART='heart',P_SHOVEL='shovel';
const POWERUP_COLORS={[P_STAR]:'#FFD700',[P_BOMB]:'#FF4444',[P_FREEZE]:'#00BFFF',[P_SHIELD]:'#FFD700',[P_HEART]:'#FF6B6B',[P_SHOVEL]:'#8B4513'};
const POWERUP_ICONS={[P_STAR]:'★',[P_BOMB]:'💣',[P_FREEZE]:'❄',[P_SHIELD]:'🛡',[P_HEART]:'♥',[P_SHOVEL]:'⛏'};

// 游戏状态
let state=S_START,score=0,hiScore=+(localStorage.tankHi||0),lives=3,level=1;
let totalEnemies=20,enemiesKilled=0;
let tiles=[],player=null,bullets=[],enemies=[],explosions=[],powerups=[],particles=[];
let floatingTexts=[],keys={},muted=false,lastTime=0,spawnTimer=0,enemySpawned=0;
let combo=0,comboTimer=0,comboMult=1;
let frozenTimer=0,shieldTimer=0,shovelTimer=0;
let powerLevel=1,shakeTimer=0,shakeIntensity=0;
let levelClearTimer=0; // 过关缓冲

// ===== 速度配置 (慢速舒适版) =====
const PLAYER_SPEED=2;           // 玩家: 2px/帧 (64px/s, 从屏幕一边到另一边13秒)
const ENEMY_STATS={
    normal:{speed:0.9,cd:2500,score:100},
    fast:  {speed:1.4,cd:1800,score:200},
    heavy: {speed:0.55,cd:3200,score:300},
    armor: {speed:0.7,cd:2600,score:400}
};
const POWERUP_DROP=0.3; // 道具掉落率30%

const ENEMY_SCORES={normal:100,fast:200,heavy:300,armor:400};

// ===== 关卡加载 =====
function loadLevel(n){
    const lv=LEVELS[(n-1)%LEVELS.length];
    tiles=lv.map(r=>r.split('').map(c=>c==='.'?EMPTY:c==='B'?BRICK:c==='S'?STEEL:c==='W'?WATER:c==='F'?FOREST:c==='X'?BASE:EMPTY));
    enemies=[];bullets=[];explosions=[];powerups=[];particles=[];floatingTexts=[];
    enemySpawned=0;enemiesKilled=0;spawnTimer=0;powerLevel=1;combo=0;comboTimer=0;
    frozenTimer=0;shieldTimer=0;shovelTimer=0;levelClearTimer=0;
    spawnPlayer();
}

function spawnPlayer(){
    // 出生点: 底部中央偏左，确保是空地
    const sx=Math.floor(COLS/2)*TILE, sy=(ROWS-5)*TILE;
    player={x:sx,y:sy,w:32,h:32,dir:0,speed:PLAYER_SPEED,cd:0,inv:3000,dead:false};
}

// 检查一个出生点周围是否空旷(3x3区域无砖/钢/水/基地)
function isSpawnClear(px,py){
    const tx=Math.floor((px+16)/TILE),ty=Math.floor((py+16)/TILE);
    for(let y=ty-1;y<=ty+1;y++){
        for(let x=tx-1;x<=tx+1;x++){
            const t=tiles[y]?.[x];
            if(t===undefined||t===BRICK||t===STEEL||t===WATER||t===BASE||t===BASE_D)return false;
        }
    }
    return true;
}

// 找一个空旷的出生点(只搜索上半场)
function findSpawnPoint(){
    // 三个经典出生位优先
    const preferred=[[TILE*2,TILE],[Math.floor(COLS/2)*TILE,TILE],[W-TILE*4,TILE]];
    for(const [px,py] of preferred){
        if(isSpawnClear(px,py))return [px,py];
    }
    // 兜底: 随机搜索上半场空地
    for(let i=0;i<200;i++){
        const px=TILE*(2+Math.floor(Math.random()*(COLS-4)));
        const py=TILE*(1+Math.floor(Math.random()*(Math.floor(ROWS/2)-1)));
        if(isSpawnClear(px,py))return [px,py];
    }
    return [TILE*2,TILE]; // 最后兜底
}

function spawnEnemy(){
    const typeCount=Math.min(2+Math.floor(level/2),5);
    const types=['normal','normal','fast','heavy','armor'].slice(0,typeCount);
    const type=types[Math.floor(Math.random()*types.length)];
    const st=ENEMY_STATS[type];
    const [sx,sy]=findSpawnPoint();
    enemies.push({x:sx,y:sy,w:32,h:32,dir:2,speed:st.speed,cd:1500,moveTimer:0,
        dead:false,type,score:ENEMY_SCORES[type],frozen:false,stuck:0,turnCd:0});
    enemySpawned++;snd.enemyAppear();
}

// ===== 碰撞检测 (修复版: AABB遍历所有覆盖瓦片, 不漏边) =====
// rect: x,y为左上角, w,h为尺寸; 返回是否与砖/钢/水/基地重叠
function collide(x,y,w,h){
    // 覆盖的瓦片范围 (用内缩1px的盒避免贴边误判)
    const L=x+1,R=x+w-1,T=y+1,B=y+h-1;
    const tx0=Math.floor(L/TILE),tx1=Math.floor(R/TILE);
    const ty0=Math.floor(T/TILE),ty1=Math.floor(B/TILE);
    for(let ty=ty0;ty<=ty1;ty++){
        for(let tx=tx0;tx<=tx1;tx++){
            const t=tiles[ty]?.[tx];
            if(t===undefined)return true; // 出界视为碰撞
            if(t===BRICK||t===STEEL||t===WATER||t===BASE||t===BASE_D)return true;
        }
    }
    return false;
}

function hitTest(a,b){return a.x<b.x+b.w&&a.x+a.w>b.x&&a.y<b.y+b.h&&a.y+a.h>b.y}

// 分轴移动: 先X后Y, 撞墙时贴墙滑行 (手感关键!)
function moveWithCollision(ent,dx,dy){
    if(dx!==0){
        const nx=ent.x+dx;
        if(!collide(nx,ent.y,ent.w,ent.h)){ent.x=nx;}
    }
    if(dy!==0){
        const ny=ent.y+dy;
        if(!collide(ent.x,ny,ent.w,ent.h)){ent.y=ny;}
    }
    // 边界夹取
    ent.x=Math.max(0,Math.min(W-ent.w,ent.x));
    ent.y=Math.max(0,Math.min(H-ent.h,ent.y));
}

// ===== 特效 =====
function addParticles(x,y,color,count=8){
    for(let i=0;i<count;i++){
        const angle=Math.random()*Math.PI*2,speed=1.5+Math.random()*2.5;
        particles.push({x,y,vx:Math.cos(angle)*speed,vy:Math.sin(angle)*speed,life:30+Math.random()*20,color,size:3+Math.random()*3});
    }
}

function addFloatingText(x,y,text,color='#FFF'){floatingTexts.push({x,y,text,color,life:60,vy:-1.5})}

function addExplosion(x,y,large){
    explosions.push({x,y,f:0,max:large?16:10,size:large?64:32});
    shakeTimer=large?8:4;shakeIntensity=large?6:3;
    if(large)addParticles(x+16,y+16,'#FF6600',10);
}

// ===== 开火 =====
function fireBullet(owner,isPlayer){
    const isHeavy=!isPlayer&&owner.type==='heavy';
    const bSize=isHeavy?8:6;
    const bSpeed=isPlayer?6:(owner.type==='fast'?7:4.5);
    const offsets=isPlayer?(powerLevel===1?[0]:powerLevel===2?[-5,5]:[-7,0,7]):[0];
    offsets.forEach(off=>{
        let bx=owner.x+owner.w/2-bSize/2+DX[owner.dir]*16;
        let by=owner.y+owner.h/2-bSize/2+DY[owner.dir]*16;
        if(off!==0){const perp=(owner.dir+1)%4;bx+=DX[perp]*off;by+=DY[perp]*off;}
        bullets.push({x:bx,y:by,dir:owner.dir,speed:bSpeed,w:bSize,h:bSize,dead:false,isPlayer,
            color:isPlayer?'#FFFF00':(isHeavy?'#FF6600':'#FF0000')});
    });
    snd.shoot(isPlayer?powerLevel:1);
}

// ===== 道具 =====
function spawnPowerup(x,y){
    if(Math.random()>POWERUP_DROP)return;
    const types=[P_STAR,P_BOMB,P_FREEZE,P_SHIELD,P_HEART,P_SHOVEL];
    const weights=[25,15,20,15,15,10];
    const total=weights.reduce((a,b)=>a+b,0);
    let r=Math.random()*total,type=types[0];
    for(let i=0;i<weights.length;i++){r-=weights[i];if(r<=0){type=types[i];break;}}
    // 道具对齐到格子中心
    const px=Math.floor(x/TILE)*TILE,py=Math.floor(y/TILE)*TILE;
    powerups.push({x:px,y:py,w:32,h:32,type,life:8000,bob:0}); // 存在8秒
}

function applyPowerup(type){
    snd.powerup();
    addFloatingText(player.x+16,player.y-8,POWERUP_ICONS[type],POWERUP_COLORS[type]);
    switch(type){
        case P_STAR:powerLevel=Math.min(3,powerLevel+1);addFloatingText(player.x+16,player.y-28,'火力+'+powerLevel,'#FFD700');break;
        case P_BOMB:{
            const count=enemies.length;
            enemies.forEach(e=>{score+=100;addExplosion(e.x,e.y,true);});
            enemies=[];enemiesKilled+=count;
            addFloatingText(W/2,H/2,'清屏! +'+(count*100),'#FF4444');break;}
        case P_FREEZE:
            frozenTimer=8000;addFloatingText(W/2,H/2-40,'❄ 冰冻!','#00BFFF');break;
        case P_SHIELD:
            shieldTimer=10000;addFloatingText(player.x+16,player.y-28,'护盾!','#FFD700');break;
        case P_HEART:
            lives=Math.min(5,lives+1);addFloatingText(player.x+16,player.y-28,'+1生命','#FF6B6B');break;
        case P_SHOVEL:
            shovelTimer=15000;protectBase();addFloatingText(W/2,H/2-40,'⛏ 基地加固!','#8B4513');break;
    }
}

function protectBase(){
    // 找基地位置, 加固周围砖墙
    for(let y=0;y<ROWS;y++)for(let x=0;x<COLS;x++){
        if(tiles[y][x]===BASE){
            for(let dy=-1;dy<=1;dy++)for(let dx=-1;dx<=1;dx++){
                const ty=y+dy,tx=x+dx;
                if(tiles[ty]?.[tx]===BRICK)tiles[ty][tx]=STEEL;
            }
            return;
        }
    }
}

function unprotectBase(){
    for(let y=0;y<ROWS;y++)for(let x=0;x<COLS;x++){
        if(tiles[y][x]===BASE){
            for(let dy=-1;dy<=1;dy++)for(let dx=-1;dx<=1;dx++){
                const ty=y+dy,tx=x+dx;
                if(tiles[ty]?.[tx]===STEEL)tiles[ty][tx]=BRICK;
            }
            return;
        }
    }
}

// ===== 敌人AI (修复版: 不抖动/不卡墙/会射击) =====
function updateEnemyAI(e,dt){
    if(frozenTimer>0){e.frozen=true;return;}
    e.frozen=false;
    e.cd=Math.max(0,e.cd-dt);
    e.turnCd=Math.max(0,(e.turnCd||0)-dt);

    // --- 移动 ---
    moveWithCollision(e,DX[e.dir]*e.speed,DY[e.dir]*e.speed);

    // 检查前方是否被堵: 被堵或转向冷却结束才考虑换向
    const lookX=e.x+DX[e.dir]*(e.speed+4),lookY=e.y+DY[e.dir]*(e.speed+4);
    const blocked=collide(lookX,lookY,e.w,e.h);

    if(e.turnCd<=0&&(blocked||Math.random()<0.01)){
        e.turnCd=400+Math.random()*400; // 换向后至少走0.4~0.8秒
        if(player&&!player.dead){
            const px=player.x-e.x,py=player.y-e.y;
            const dist=Math.hypot(px,py);
            const dirToPlayer=Math.abs(px)>Math.abs(py)?(px>0?1:3):(py>0?2:0);
            if(e.type==='normal'){
                // 普通: 60%朝玩家, 40%随机
                e.dir=Math.random()<0.6?dirToPlayer:Math.floor(Math.random()*4);
            } else if(e.type==='fast'){
                // 快速: 80%朝玩家
                e.dir=Math.random()<0.8?dirToPlayer:Math.floor(Math.random()*4);
            } else if(e.type==='heavy'){
                // 重装: 始终朝玩家
                e.dir=dirToPlayer;
            } else {
                // 装甲: 50%朝玩家, 50%随机巡逻
                e.dir=Math.random()<0.5?dirToPlayer:Math.floor(Math.random()*4);
            }
        } else {
            e.dir=Math.floor(Math.random()*4);
        }
    }

    // --- 射击 ---
    if(e.cd<=0&&player&&!player.dead){
        const px=player.x-e.x,py=player.y-e.y;
        // 只有大致朝向玩家时才开火(与玩家同轴或在朝向方向上)
        const alignedX=Math.abs(py)<24, alignedY=Math.abs(px)<24;
        const facing=(e.dir===1&&px>0)||(e.dir===3&&px<0)||(e.dir===2&&py>0)||(e.dir===0&&py<0);
        const onAxis=(e.dir===1||e.dir===3)?alignedX:alignedY;
        if(onAxis&&facing&&Math.random()<0.8){
            fireBullet(e,false);
            e.cd=ENEMY_STATS[e.type].cd;
        } else if(Math.random()<0.02){
            // 偶尔盲射
            fireBullet(e,false);
            e.cd=ENEMY_STATS[e.type].cd;
        }
    }
}

// ===== 主更新 =====
function update(dt){
    if(state!==S_PLAY)return;

    // 全局计时器
    if(frozenTimer>0)frozenTimer-=dt;
    if(shieldTimer>0)shieldTimer-=dt;
    if(shovelTimer>0){
        shovelTimer-=dt;
        if(shovelTimer<=0)unprotectBase();
    }
    if(shakeTimer>0)shakeTimer--;
    if(comboTimer>0){comboTimer-=dt;if(comboTimer<=0){combo=0;comboMult=1;}}

    // --- 玩家 ---
    if(!player.dead){
        player.cd=Math.max(0,player.cd-dt);
        player.inv=Math.max(0,player.inv-dt);
        let dx=0,dy=0;
        if(keys.ArrowUp||keys.KeyW){dy=-1;player.dir=0;}
        else if(keys.ArrowDown||keys.KeyS){dy=1;player.dir=2;}
        else if(keys.ArrowLeft||keys.KeyA){dx=-1;player.dir=3;}
        else if(keys.ArrowRight||keys.KeyD){dx=1;player.dir=1;}
        if(dx!==0||dy!==0)moveWithCollision(player,dx*player.speed,dy*player.speed);
        if(keys.Space&&player.cd<=0){fireBullet(player,true);player.cd=280;}
    } else {
        // 玩家死亡后的复活计时
        player.respawnTimer=(player.respawnTimer||0)+dt;
        if(player.respawnTimer>2000&&lives>0)spawnPlayer();
    }

    // --- 敌人生成 ---
    const alive=enemies.length;
    if(spawnTimer>1800&&enemySpawned<totalEnemies&&alive<4){
        spawnEnemy();spawnTimer=0;
    }
    spawnTimer+=dt;

    // --- 敌人 ---
    enemies.forEach(e=>updateEnemyAI(e,dt));

    // --- 子弹 ---
    bullets.forEach(b=>{
        if(b.dead)return;
        // 子弹分步移动防止高速穿墙
        const steps=Math.ceil(b.speed/4);
        for(let i=0;i<steps&&!b.dead;i++){
            b.x+=DX[b.dir]*b.speed/steps;
            b.y+=DY[b.dir]*b.speed/steps;
            // 出界
            if(b.x<-8||b.x>W||b.y<-8||b.y>H){b.dead=true;break;}
            // 撞地形
            const tx=Math.floor((b.x+b.w/2)/TILE),ty=Math.floor((b.y+b.h/2)/TILE);
            const tile=tiles[ty]?.[tx];
            if(tile===BRICK){
                tiles[ty][tx]=EMPTY;b.dead=true;
                addExplosion(tx*TILE,ty*TILE,false);
                addParticles(tx*TILE+16,ty*TILE+16,'#C84C0C',6);
                snd.hit();break;
            } else if(tile===STEEL){
                b.dead=true;
                addParticles(b.x,b.y,'#FFD700',4);
                snd.hit();break;
            } else if(tile===BASE){
                tiles[ty][tx]=BASE_D;b.dead=true;
                addExplosion(tx*TILE,ty*TILE,true);
                state=S_OVER;snd.over();break;
            }
        }
        if(b.dead)return;
        // 玩家子弹 vs 敌人
        if(b.isPlayer){
            for(const e of enemies){
                if(!e.dead&&hitTest(b,e)){
                    b.dead=true;e.dead=true;
                    combo++;comboTimer=3000;
                    comboMult=Math.min(4,1+Math.floor(combo/3));
                    const pts=e.score*comboMult*(frozenTimer>0?2:1);
                    score+=pts;enemiesKilled++;
                    addExplosion(e.x+16,e.y+16,true);
                    addFloatingText(e.x+16,e.y,'+'+pts,comboMult>1?'#FF6B6B':'#FFF');
                    if(comboMult>1)addFloatingText(e.x+16,e.y-20,combo+'连击 x'+comboMult,'#FFD700');
                    spawnPowerup(e.x,e.y);
                    snd.boom();
                    break;
                }
            }
        } else {
            // 敌人子弹 vs 玩家
            if(!player.dead&&player.inv<=0&&shieldTimer<=0&&hitTest(b,player)){
                b.dead=true;
                lives--;
                if(lives<=0){
                    addExplosion(player.x,player.y,true);
                    player.dead=true;
                    state=S_OVER;snd.over();
                } else {
                    addExplosion(player.x,player.y,true);
                    snd.boom();
                    player.dead=true;
                    player.respawnTimer=0;
                }
            }
        }
    });
    bullets=bullets.filter(b=>!b.dead);
    enemies=enemies.filter(e=>!e.dead);

    // --- 特效 ---
    explosions.forEach(ex=>ex.f++);
    explosions=explosions.filter(ex=>ex.f<ex.max);
    particles.forEach(p=>{p.x+=p.vx;p.y+=p.vy;p.vy+=0.15;p.life--;});
    particles=particles.filter(p=>p.life>0);
    floatingTexts.forEach(ft=>{ft.y+=ft.vy;ft.life--;});
    floatingTexts=floatingTexts.filter(ft=>ft.life>0);

    // --- 道具 ---
    powerups.forEach(p=>{
        p.life-=dt;p.bob=(p.bob||0)+0.08;
        if(player&&!player.dead&&hitTest(p,player)){applyPowerup(p.type);p.life=0;}
    });
    powerups=powerups.filter(p=>p.life>0);

    // --- 过关判定 ---
    if(enemySpawned>=totalEnemies&&enemies.length===0&&state===S_PLAY){
        if(lives===3){score+=1000;addFloatingText(W/2,H/2,'完美过关! +1000','#FFD700');}
        snd.levelup();
        level++;
        setTimeout(()=>{if(state===S_PLAY)loadLevel(level);},1500);
    }

    // --- 最高分 ---
    if(score>hiScore){hiScore=score;localStorage.tankHi=hiScore;}
}
