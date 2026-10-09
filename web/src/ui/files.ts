import type { UploadFile } from "antd";

/** 选中的文件转成接口要的 {name, content(base64)}：上传文档、题目文件都走这里 */
function toBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result).split(",", 2)[1] ?? "");
    reader.onerror = () => reject(reader.error);
    reader.readAsDataURL(file);
  });
}

export const encodeFiles = async (files: UploadFile[]) =>
  Promise.all(files.map(async (f) => ({ name: f.name, content: await toBase64(f.originFileObj as File) })));
