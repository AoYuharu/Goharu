// 坦克大战 - 入口 (焕新版)
const overlay=document.getElementById('o');
const muteBtn=document.getElementById('m');

document.addEventListener('keydown',e=>{
    keys[e.code]=true;
    if(e.code==='Space'){
        e.preventDefault();
        initAudio();
        if(state===S_START||state===S_OVER||state===S_WIN){
            score=0;lives=3;level=1;
            loadLevel(level);
            state=S_PLAY;
            overlay.classList.add('hidden');
        }
    }
    if(e.code==='KeyR'&&state===S_OVER){
        score=0;lives=3;level=1;
        loadLevel(level);
        state=S_PLAY;
    }
    if(e.code==='KeyP'&&(state===S_PLAY||state===S_PAUSE))state=state===S_PLAY?S_PAUSE:S_PLAY;
    if(e.code==='KeyM'){muted=!muted;muteBtn.textContent=muted?'🔇':'🔊'}
});
document.addEventListener('keyup',e=>{keys[e.code]=false});
muteBtn.onclick=()=>{muted=!muted;muteBtn.textContent=muted?'🔇':'🔊'};

function gameLoop(ts){const dt=ts-lastTime;lastTime=ts;update(dt);draw();requestAnimationFrame(gameLoop)}

loadLevel(1);requestAnimationFrame(gameLoop);
