import { contextSnapshot, runWithContext } from "./context.js";

type AnyFunction = (...args: any[]) => any;

export function track<T extends AnyFunction>(fn: T): T;
export function track<T extends AnyFunction>(name: string, fn: T): T;
export function track<T extends AnyFunction>(nameOrFn: string | T, maybeFn?: T): T {
  const fn = typeof nameOrFn === "string" ? maybeFn : nameOrFn;
  if (typeof fn !== "function") throw new TypeError("track requires a function");
  const funcName = typeof nameOrFn === "string" ? nameOrFn : fn.name || "<anonymous>";
  const wrapped = function (this: unknown, ...args: unknown[]) {
    return runWithContext({ ...contextSnapshot(), funcName }, () => fn.apply(this, args));
  };
  return wrapped as unknown as T;
}
