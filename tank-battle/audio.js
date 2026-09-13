// 坦克大战 - 音效系统 (焕新版)
let audio=null;
function initAudio(){if(!audio)audio=new(window.AudioContext||webkitAudioContext)()}
function beep(f,d,t='square',vol=0.12){if(!audio||muted)return;try{const o=audio.createOscillator(),g=audio.createGain();o.connect(g);g.connect(audio.destination);o.type=t;o.frequency.value=f;g.gain.setValueAtTime(vol,audio.currentTime);g.gain.exponentialRampToValueAtTime(.01,audio.currentTime+d);o.start();o.stop(audio.currentTime+d)}catch(e){}}

const snd={
    shoot:(level=1)=>{
        const freqs={1:600,2:800,3:1000};
        beep(freqs[level]||600,.08,'square',0.1);
    },
    boom:()=>{
        beep(80,.4,'sawtooth',0.15);
        beep(60,.3,'square',0.08);
    },
    hit:()=>beep(300,.05,'square',0.06),
    powerup:()=>{
        beep(400,.1,'sine',0.1);
        setTimeout(()=>beep(600,.1,'sine',0.1),80);
        setTimeout(()=>beep(800,.15,'sine',0.1),160);
    },
    over:()=>{
        beep(300,.2,'square',0.12);
        setTimeout(()=>beep(200,.2,'square',0.1),200);
        setTimeout(()=>beep(100,.5,'sawtooth',0.15),400);
    },
    levelup:()=>{
        const notes=[523,659,784,1047];
        notes.forEach((f,i)=>setTimeout(()=>beep(f,.2,'square',0.1),i*120));
    },
    enemyAppear:()=>{
        beep(200,.1,'square',0.05);
        setTimeout(()=>beep(400,.1,'square',0.05),50);
    },
    freeze:()=>{
        beep(200,.3,'sine',0.08);
        setTimeout(()=>beep(150,.3,'sine',0.06),100);
    },
    shield:()=>beep(1000,.1,'sine',0.08),
    combo:(n)=>{
        const baseFreq=400+n*100;
        beep(baseFreq,.08,'square',0.1);
    }
};
