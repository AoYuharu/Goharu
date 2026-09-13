const fs=require('fs');
const vm=require('vm');
const code=fs.readFileSync('levels.js','utf8');
// const在vm顶层不会挂到sandbox对象上, 用追加导出语句解决
const patched=code+'\n;globalThis.__LEVELS=LEVELS;';
const sandbox={};
vm.createContext(sandbox);
vm.runInContext(patched,sandbox);
const LEVELS=sandbox.__LEVELS;
console.log('关卡数:',LEVELS.length);
let problems=0;

LEVELS.forEach((lv,i)=>{
    if(lv.length!==20){console.log(`L${i+1}: 行数${lv.length}错误`);problems++;}
    lv.forEach((row,y)=>{ if(row.length!==26){console.log(`L${i+1} 行${y}: 列数${row.length}错误`);problems++;} });
    for(const row of lv)for(const c of row){
        if(!'BSWFX.'.includes(c)){console.log(`L${i+1}: 非法字符'${c}'`);problems++;}
    }
    let hasBase=false;
    lv.forEach(row=>{for(const c of row){if(c==='X')hasBase=true;}});
    if(!hasBase){console.log(`L${i+1}: 无基地!`);problems++;}

    const px=13,py=15;
    let clear=true;
    for(let dy=-1;dy<=1;dy++)for(let dx=-1;dx<=1;dx++){
        const c=lv[py+dy]?.[px+dx];
        if(c==='B'||c==='S'||c==='W'||c==='X')clear=false;
    }    if(!clear){console.log(`L${i+1}: 玩家出生点(${px},${py})被堵!`);problems++;}

    for(const ex of [2,13,23]){
        let eClear=true;
        for(let dy=0;dy<=2;dy++)for(let dx=-1;dx<=1;dx++){
            const c=lv[1+dy]?.[ex+dx];
            if(c==='B'||c==='S'||c==='W')eClear=false;
        }
        if(!eClear){console.log(`L${i+1}: 敌人出生点(${ex},1)被堵!`);problems++;}
    }
});

console.log(problems===0?'全部关卡验证通过 ✓':'发现问题: '+problems);
process.exit(problems===0?0:1);
