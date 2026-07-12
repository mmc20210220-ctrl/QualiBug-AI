export type BehaviorFieldFrame = {
  width: number;
  height: number;
  time: number;
  pointerX: number;
  pointerY: number;
  focusBoost: number;
  reducedMotion: boolean;
};

export const BEHAVIOR_NODES = [
  { u: 0.08, v: 0.74 },
  { u: 0.34, v: 0.58 },
  { u: 0.62, v: 0.35 },
  { u: 0.9, v: 0.48 },
] as const;

export const BEHAVIOR_TRAJECTORY = BEHAVIOR_NODES;

type Point = {
  x: number;
  y: number;
};

function project(
  u: number,
  v: number,
  origin: Point,
  scaleX: number,
  scaleY: number,
): Point {
  return {
    x: origin.x + (u - v) * scaleX,
    y: origin.y + (u + v) * scaleY,
  };
}

export function drawPerspectivePlane(
  context: CanvasRenderingContext2D,
  frame: BehaviorFieldFrame,
): Point[] {
  const driftX = (frame.pointerX - 0.5) * 18;
  const driftY = (frame.pointerY - 0.5) * 10;
  const origin = {
    x: frame.width * 0.2 + driftX,
    y: frame.height * 0.48 + driftY,
  };
  const scaleX = Math.min(frame.width * 0.38, 340);
  const scaleY = Math.min(frame.height * 0.18, 115);
  const corners = [
    project(0, 0, origin, scaleX, scaleY),
    project(1, 0, origin, scaleX, scaleY),
    project(1, 1, origin, scaleX, scaleY),
    project(0, 1, origin, scaleX, scaleY),
  ];

  context.save();
  const planeFill = context.createLinearGradient(
    corners[3].x,
    corners[3].y,
    corners[1].x,
    corners[1].y,
  );
  planeFill.addColorStop(0, 'rgba(37,99,235,.16)');
  planeFill.addColorStop(1, 'rgba(45,212,191,.025)');
  context.beginPath();
  context.moveTo(corners[0].x, corners[0].y);
  corners.slice(1).forEach((corner) => context.lineTo(corner.x, corner.y));
  context.closePath();
  context.fillStyle = planeFill;
  context.fill();
  context.strokeStyle = 'rgba(56,189,248,.32)';
  context.lineWidth = 1.3;
  context.stroke();

  context.strokeStyle = 'rgba(56,189,248,.18)';
  context.lineWidth = 1;
  for (let index = 0; index <= 6; index += 1) {
    const ratio = index / 6;
    const a = project(ratio, 0, origin, scaleX, scaleY);
    const b = project(ratio, 1, origin, scaleX, scaleY);
    const c = project(0, ratio, origin, scaleX, scaleY);
    const d = project(1, ratio, origin, scaleX, scaleY);
    context.beginPath();
    context.moveTo(a.x, a.y);
    context.lineTo(b.x, b.y);
    context.stroke();
    context.beginPath();
    context.moveTo(c.x, c.y);
    context.lineTo(d.x, d.y);
    context.stroke();
  }
  context.restore();

  return BEHAVIOR_NODES.map((node) => (
    project(node.u, node.v, origin, scaleX, scaleY)
  ));
}

export function drawBehaviorFieldFrame(
  context: CanvasRenderingContext2D,
  frame: BehaviorFieldFrame,
): void {
  context.clearRect(0, 0, frame.width, frame.height);
  const nodes = drawPerspectivePlane(context, frame);

  context.save();
  context.strokeStyle = '#7DD3FC';
  context.lineWidth = 2.5 + frame.focusBoost;
  context.lineCap = 'round';
  context.shadowColor = 'rgba(14,165,233,.45)';
  context.shadowBlur = 8 + frame.focusBoost * 8;
  context.beginPath();
  context.moveTo(nodes[0].x, nodes[0].y);
  context.bezierCurveTo(
    nodes[1].x - 18,
    nodes[1].y + 8,
    nodes[1].x - 8,
    nodes[1].y + 2,
    nodes[1].x,
    nodes[1].y,
  );
  context.bezierCurveTo(
    nodes[2].x - 12,
    nodes[2].y + 14,
    nodes[2].x - 8,
    nodes[2].y + 2,
    nodes[2].x,
    nodes[2].y,
  );
  context.bezierCurveTo(
    nodes[3].x - 12,
    nodes[3].y - 8,
    nodes[3].x - 5,
    nodes[3].y,
    nodes[3].x,
    nodes[3].y,
  );
  context.stroke();
  context.shadowBlur = 0;

  nodes.forEach((node, index) => {
    const pulse = frame.reducedMotion
      ? 0
      : Math.sin(frame.time * 0.002 + index * 0.8) * 0.8;
    context.beginPath();
    context.arc(
      node.x,
      node.y,
      4.5 + pulse + frame.focusBoost * 0.5,
      0,
      Math.PI * 2,
    );
    context.fillStyle = index === 2 ? '#2DD4BF' : '#0EA5E9';
    context.fill();
  });
  context.restore();
}
