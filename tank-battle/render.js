// 坦克大战 - 渲染 (焕新版)
const canvas=document.getElementById('c');
canvas.width=W;canvas.height=H;
const ctx=canvas.getContext('2d');
let animFrame=0;

function draw(){
    animFrame++;
    ctx.save();
    if(shakeTimer>0)ctx.translate(Math.random()*shakeIntensity-shakeIntensity/2,Math.random()*shakeIntensity-shakeIntensity/2);
    ctx.fillStyle='#000';ctx.fillRect(0,0,W,H);
    
    for(let y=0;y<ROWS;y++)for(let x=0;x<COLS;x++){
        const t=tiles[y]?.[x],px=x*TILE,py=y*TILE;
        if(t===BRICK){
            ctx.fillStyle='#C84C0C';ctx.fillRect(px,py,TILE,TILE);
            ctx.strokeStyle='#8B3000';ctx.lineWidth=1;
            for(let r=0;r<4;r++){const off=r%2===0?0:8;ctx.strokeRect(px+off,py+r*8,16,8);}
            ctx.fillStyle='#A03A08';
            ctx.fillRect(px+2,py+2,4,4);ctx.fillRect(px+14,py+10,4,4);
            ctx.fillRect(px+26,py+6,4,4);ctx.fillRect(px+6,py+22,4,4);ctx.fillRect(px+22,py+26,4,4);
        } else if(t===STEEL){
            ctx.fillStyle='#B8B8B8';ctx.fillRect(px,py,TILE,TILE);
            const grad=ctx.createLinearGradient(px,py,px+TILE,py+TILE);
            grad.addColorStop(0,'rgba(255,255,255,0.3)');grad.addColorStop(1,'rgba(0,0,0,0.2)');
            ctx.fillStyle=grad;ctx.fillRect(px,py,TILE,TILE);
            ctx.strokeStyle='#888';ctx.lineWidth=2;ctx.strokeRect(px+2,py+2,TILE-4,TILE-4);
            ctx.fillStyle='#666';ctx.fillRect(px+4,py+4,4,4);ctx.fillRect(px+24,py+4,4,4);
            ctx.fillRect(px+4,py+24,4,4);ctx.fillRect(px+24,py+24,4,4);
        } else if(t===WATER){
            ctx.fillStyle='#0066CC';ctx.fillRect(px,py,TILE,TILE);
            ctx.fillStyle='#0088FF';
            const wave=Math.sin(animFrame/10+x)*4;
            ctx.fillRect(px+4+wave,py+6,12,3);ctx.fillRect(px+12-wave,py+14,14,3);ctx.fillRect(px+6+wave,py+22,10,3);
        } else if(t===FOREST){
            ctx.fillStyle='#1B5E20';ctx.fillRect(px,py,TILE,TILE);
            ctx.fillStyle='#228B22';ctx.beginPath();ctx.arc(px+16,py+14,10,0,Math.PI*2);ctx.fill();
            ctx.fillStyle='#2E7D32';ctx.beginPath();ctx.arc(px+12,py+12,6,0,Math.PI*2);ctx.fill();
            ctx.fillStyle='#388E3C';ctx.beginPath();ctx.arc(px+20,py+16,5,0,Math.PI*2);ctx.fill();
            ctx.fillStyle='#5D4037';ctx.fillRect(px+14,py+20,4,8);
        } else if(t===BASE||t===BASE_D){
            ctx.fillStyle='#1B5E20';ctx.fillRect(px,py,TILE,TILE);
            if(t===BASE){
                ctx.fillStyle='#8B4513';ctx.fillRect(px+14,py+6,4,22);
                const wave=Math.sin(animFrame/8)*3;
                ctx.fillStyle='#FFD700';ctx.beginPath();
                ctx.moveTo(px+18,py+6+wave);ctx.lineTo(px+30,py+12);ctx.lineTo(px+18,py+18-wave);ctx.closePath();ctx.fill();
            } else {
                ctx.fillStyle='#8B0000';ctx.fillRect(px+14,py+6,4,22);ctx.fillRect(px+18,py+10,10,8);
            }
        }
    }
    
    particles.forEach(p=>{ctx.fillStyle=p.color;ctx.globalAlpha=p.life/50;ctx.fillRect(p.x-p.size/2,p.y-p.size/2,p.size,p.size);});ctx.globalAlpha=1;
    
    explosions.forEach(ex=>{
        const progress=ex.f/ex.max;
        const colors=['#FFF','#FFFF00','#FFA500','#FF4500','#FF0000','#8B0000','#555'];
        const colorIdx=Math.min(Math.floor(progress*7),6);
        ctx.fillStyle=colors[colorIdx];ctx.globalAlpha=1-progress;
        const r=ex.size/2*Math.sin(progress*Math.PI);
        ctx.beginPath();ctx.arc(ex.x+ex.size/2,ex.y+ex.size/2,r,0,Math.PI*2);ctx.fill();
        if(progress<0.5){ctx.fillStyle='rgba(255,255,200,0.5)';ctx.beginPath();ctx.arc(ex.x+ex.size/2,ex.y+ex.size/2,r*0.6,0,Math.PI*2);ctx.fill();}
    });ctx.globalAlpha=1;
    
    powerups.forEach(p=>{
        const bob=Math.sin(p.bob)*4,alpha=Math.sin(animFrame/5)*0.3+0.7;
        ctx.globalAlpha=alpha;
        ctx.fillStyle='#333';ctx.beginPath();ctx.arc(p.x+16,p.y+16+bob,14,0,Math.PI*2);ctx.fill();
        ctx.fillStyle=POWERUP_COLORS[p.type];ctx.beginPath();ctx.arc(p.x+16,p.y+16+bob,12,0,Math.PI*2);ctx.fill();
        ctx.fillStyle='#FFF';ctx.font='bold 16px Arial';ctx.textAlign='center';
        ctx.fillText(POWERUP_ICONS[p.type],p.x+16,p.y+21+bob);
    });ctx.globalAlpha=1;ctx.textAlign='left';
    
    bullets.forEach(b=>{
        ctx.fillStyle=b.color||(b.isPlayer?'#FFFF00':'#FF0000');
        ctx.beginPath();ctx.arc(b.x+b.w/2,b.y+b.h/2,b.w/2,0,Math.PI*2);ctx.fill();
        ctx.fillStyle=b.isPlayer?'rgba(255,255,0,0.3)':'rgba(255,0,0,0.3)';
        ctx.beginPath();ctx.arc(b.x+b.w/2,b.y+b.h/2,b.w,0,Math.PI*2);ctx.fill();
    });
    
    enemies.forEach(e=>{
        const baseColor=e.type==='fast'?'#2196F3':e.type==='heavy'?'#D32F2F':e.type==='armor'?'#9E9E9E':'#689F38';
        drawTank(e,baseColor,false,e.frozen);
    });
    
    if(player&&!player.dead&&(player.inv<=0||Math.floor(player.inv/80)%2===0)){
        if(shieldTimer>0){ctx.strokeStyle='rgba(255,215,0,0.6)';ctx.lineWidth=3;ctx.beginPath();ctx.arc(player.x+16,player.y+16,20+Math.sin(animFrame/5)*2,0,Math.PI*2);ctx.stroke();}
        drawTank(player,'#4CAF50',true,false);
    }
    
    floatingTexts.forEach(ft=>{ctx.fillStyle=ft.color;ctx.globalAlpha=ft.life/60;ctx.font='bold 14px Courier New';ctx.textAlign='center';ctx.fillText(ft.text,ft.x,ft.y);});ctx.globalAlpha=1;ctx.textAlign='left';
    
    drawHUD();
    if(state===S_PAUSE){ctx.fillStyle='rgba(0,0,0,.7)';ctx.fillRect(0,0,W,H);ctx.fillStyle='#FF0';ctx.font='bold 36px Courier New';ctx.textAlign='center';ctx.fillText('PAUSE',W/2,H/2);ctx.textAlign='left';}
    if(state===S_OVER){ctx.fillStyle='rgba(0,0,0,.85)';ctx.fillRect(0,0,W,H);ctx.fillStyle='#F00';ctx.font='bold 48px Courier New';ctx.textAlign='center';ctx.fillText('GAME OVER',W/2,H/2-40);ctx.fillStyle='#FFF';ctx.font='24px Courier New';ctx.fillText('得分: '+score,W/2,H/2+10);ctx.fillText('按 [R] 重新开始',W/2,H/2+50);ctx.textAlign='left';if(score>hiScore)localStorage.tankHi=hiScore=score;}
    if(state===S_WIN){ctx.fillStyle='rgba(0,0,0,.8)';ctx.fillRect(0,0,W,H);ctx.fillStyle='#FFD700';ctx.font='bold 36px Courier New';ctx.textAlign='center';ctx.fillText('过关!',W/2,H/2);ctx.fillText('按空格继续',W/2,H/2+40);ctx.textAlign='left';}
    ctx.restore();
}

function drawTank(t,color,isPlayer,isFrozen){
    ctx.save();ctx.translate(t.x+t.w/2,t.y+t.h/2);ctx.rotate(t.dir*Math.PI/2);
    if(isFrozen){ctx.fillStyle='#88CCFF';ctx.globalAlpha=0.5;ctx.fillRect(-16,-16,32,32);ctx.globalAlpha=1;}
    ctx.fillStyle='#444';ctx.fillRect(-16,-14,32,6);ctx.fillRect(-16,8,32,6);
    ctx.fillStyle='#333';for(let i=-14;i<=14;i+=4){ctx.fillRect(i,-14,2,6);ctx.fillRect(i,8,2,6);}
    ctx.fillStyle=color;
    if(t.type==='heavy')ctx.fillRect(-14,-10,28,20);
    else if(t.type==='armor'){ctx.fillRect(-12,-12,24,24);ctx.strokeStyle='#666';ctx.lineWidth=2;ctx.strokeRect(-14,-14,28,28);}
    else ctx.fillRect(-12,-12,24,24);
    if(isPlayer){ctx.fillStyle='#2E7D32';ctx.fillRect(-12,-12,6,24);ctx.fillRect(6,-12,6,24);ctx.fillStyle='#388E3C';ctx.beginPath();ctx.arc(0,0,6,0,Math.PI*2);ctx.fill();}
    else{ctx.fillStyle='#333';ctx.fillRect(-12,-12,6,24);ctx.fillRect(6,-12,6,24);
        if(t.type==='fast'){ctx.fillStyle='#FF5722';ctx.fillRect(-8,4,16,4);}
        else if(t.type==='heavy'){ctx.fillStyle='#8B0000';ctx.fillRect(-10,6,20,4);}
        else if(t.type==='armor'){ctx.fillStyle='#555';ctx.strokeRect(-10,-10,20,20);}}
    ctx.fillStyle=isPlayer?'#1B5E20':'#222';
    if(t.type==='heavy'){ctx.fillRect(-3,-18,6,18);ctx.fillStyle='#111';ctx.fillRect(-2,-20,4,4);}
    else{ctx.fillRect(-2,-16,4,16);ctx.fillStyle='#111';ctx.fillRect(-1,-18,2,3);}
    ctx.restore();
}

function drawHUD(){
    ctx.fillStyle='rgba(0,0,0,0.8)';ctx.fillRect(0,0,W,30);
    ctx.fillStyle='#0F0';ctx.font='bold 16px Courier New';
    ctx.fillText('SCORE',10,12);ctx.fillText(score.toString().padStart(6,'0'),10,26);
    ctx.fillText('HI',120,12);ctx.fillText(hiScore.toString().padStart(6,'0'),120,26);
    ctx.fillStyle='#FFD700';ctx.fillText('火力',220,12);
    for(let i=0;i<3;i++){ctx.fillStyle=i<powerLevel?'#FFD700':'#333';ctx.fillText(i<powerLevel?'★':'☆',220+i*16,26);}
    ctx.fillStyle='#FF6B6B';ctx.fillText('生命',340,12);
    for(let i=0;i<5;i++){ctx.fillStyle=i<lives?'#FF6B6B':'#333';ctx.fillText(i<lives?'♥':'♡',340+i*16,26);}
    if(shieldTimer>0)ctx.fillText('🛡',500,20);
    if(frozenTimer>0)ctx.fillText('❄',530,20);
    if(combo>1){ctx.fillStyle='#FFD700';ctx.fillText('x'+comboMult,560,20);}
    ctx.fillStyle='#FFF';ctx.fillText('关卡',W-120,12);ctx.fillText(level.toString().padStart(2,'0'),W-120,26);
    ctx.fillStyle='rgba(0,0,0,0.8)';ctx.fillRect(0,H-30,W,30);
    ctx.fillStyle='#888';ctx.fillText('敌人',10,H-16);
    const barW=150,barX=60;
    ctx.fillStyle='#333';ctx.fillRect(barX,H-22,barW,14);
    ctx.fillStyle='#FF4444';ctx.fillRect(barX,H-22,barW*(enemySpawned/totalEnemies),14);
    ctx.fillStyle='#FFF';ctx.font='12px Courier New';
    ctx.fillText(enemiesKilled+'/'+totalEnemies,barX+barW+10,H-12);
    if(combo>1){ctx.fillStyle='#FFD700';ctx.font='bold 14px Courier New';ctx.fillText(combo+'连击! x'+comboMult,W/2-50,H-12);}
}
