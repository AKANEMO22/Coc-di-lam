const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const root = path.resolve(__dirname, '..');
test('speech never fully closes until the gesture ends and cycles more slowly', () => {
  const file = path.join(root, 'gestures.js');
  assert.ok(fs.existsSync(file), 'gesture timing module must exist');
  const {speechMouth} = require(file);
  const values = Array.from({length:8000},(_,ms)=>speechMouth(ms/1000));
  assert.ok(Math.min(...values) >= .399 && Math.min(...values) <= .401);
  assert.ok(Math.max(...values) <= .951);
  assert.ok(speechMouth(1/.9) < .401);
  assert.ok(speechMouth(.5/.9) > .94);
});
test('disagreement is repeatable, faster than 3.2 seconds and finishes centered', () => {
  const file = path.join(root, 'gestures.js');
  assert.ok(fs.existsSync(file), 'gesture timing module must exist');
  const {disagreementPose,DISAGREE_SECONDS} = require(file);
  assert.equal(DISAGREE_SECONDS,2.4);
  assert.deepEqual(disagreementPose(0),{yaw:0,pitch:0});
  assert.equal(disagreementPose(3).yaw,0);
  const first = Array.from({length:241},(_,i)=>disagreementPose(i/100));
  const second = Array.from({length:241},(_,i)=>disagreementPose(i/100));
  assert.deepEqual(first,second);
  assert.ok(first.some(p=>p.yaw>7));
  assert.ok(first.some(p=>p.yaw< -7));
});
test('thinking preserves two equal nods while slowing the complete flow by 35 percent', () => {
  const file = path.join(root, 'assets/model/Motions/Think_Double.motion3.json');
  assert.ok(fs.existsSync(file), 'double-nod motion must exist');
  const data = JSON.parse(fs.readFileSync(file));
  const original = JSON.parse(fs.readFileSync(path.join(root,'assets/model/Motions/Head_Nod.motion3.json')));
  assert.equal(data.Meta.Duration,2.6325);
  assert.equal(data.Meta.Loop,false);
  assert.equal(data.Meta.FadeInTime,0);
  assert.equal(data.Meta.FadeOutTime,0);
  assert.equal(data.Curves.length,original.Curves.length);
  for(let i=0;i<data.Curves.length;i++){
    const segments=data.Curves[i].Segments;
    const points=[[segments[0],segments[1]]];
    for(let j=2;j<segments.length;j+=3){assert.equal(segments[j],0);points.push([segments[j+1],segments[j+2]]);}
    const first=points.filter(p=>p[0]<1.35),second=points.filter(p=>p[0]>=1.35);
    assert.equal(first.length,second.length);
    for(let p=0;p<first.length;p++){
      assert.ok(Math.abs(second[p][0]-first[p][0]-1.35)<1e-6);
      assert.equal(second[p][1],first[p][1]);
    }
    if(data.Curves[i].Id==='ParamAngleYTrack'){
      const minimum=Math.min(...first.map(p=>p[1]));
      assert.ok(minimum< -15&&minimum> -17,'each nod retains the original player fade envelope');
    }
  }
});
